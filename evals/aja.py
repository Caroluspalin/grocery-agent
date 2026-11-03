"""Ajaa eval-tapaukset oikeaa agenttia ja OpenAI API:a vasten. MAKSAA RAHAA.

Ei ole osa pytestiä. Käyttö projektin juuresta:
    .venv/bin/python -m evals.aja                      # kaikki tapaukset
    .venv/bin/python -m evals.aja --vain banaanin_hinta mahdoton_budjetti
    .venv/bin/python -m evals.aja --malli <malli>      # vertaa malleja
    .venv/bin/python -m evals.aja -v                   # näytä työkalukutsut
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import openai

from evals.tarkistukset import Tuotetiedot, tarkista, validoi_tapaukset
from ruokaostos import config, db
from ruokaostos.agent import VIRHE_ETULIITE, Agentti

KANSIO = Path(__file__).resolve().parent
TAPAUKSET = KANSIO / "tapaukset.json"
TULOKSET = KANSIO / "tulokset"


class Mittari:
    """Käärii OpenAI-asiakkaan: laskee mallikutsut ja tokenit muuttamatta agenttia."""

    def __init__(self, client):
        self._create = client.chat.completions.create
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.kierrokset = 0
        self.syote_tokenit = 0
        self.tuloste_tokenit = 0

    def create(self, **kwargs):
        vastaus = self._create(**kwargs)
        self.kierrokset += 1
        if vastaus.usage:
            self.syote_tokenit += vastaus.usage.prompt_tokens or 0
            self.tuloste_tokenit += vastaus.usage.completion_tokens or 0
        return vastaus


def kutsut_historiasta(viestit: list[dict]) -> list[dict]:
    """Yhdistää assistentin tool_calls-kutsut ja tool-viestien tulokset id:n perusteella."""
    avoimet = {}
    kutsut = []
    for v in viestit:
        for k in v.get("tool_calls") or []:
            avoimet[k["id"]] = k["function"]
        if v["role"] == "tool":
            f = avoimet.pop(v["tool_call_id"], {"name": "?", "arguments": ""})
            try:
                argumentit = json.loads(f["arguments"] or "{}")
            except json.JSONDecodeError:
                argumentit = None
            kutsut.append(
                {
                    "nimi": f["name"],
                    "argumentit": argumentit if isinstance(argumentit, dict) else None,
                    "tulos": v["content"],
                    "virhe": v["content"].startswith(VIRHE_ETULIITE),
                }
            )
    return kutsut


def aja_tapaus(tapaus: dict, client, conn, malli: str) -> dict:
    """Ajaa yhden tapauksen tuoreella agentilla ja palauttaa jäljen."""
    mittari = Mittari(client)
    agentti = Agentti(mittari, conn, model=malli)
    alku = time.monotonic()
    vastaus, virhe = None, None
    try:
        vastaus = agentti.kysy(tapaus["kysymys"])
    except openai.AuthenticationError:
        raise
    except Exception as e:
        # Kierrosraja, katkennut vastaus tai API-virhe: tapaus epäonnistuu, ajo jatkuu.
        # Huom: agentti palauttaa historian virheen jälkeen, joten kutsut puuttuvat jäljestä.
        virhe = f"{type(e).__name__}: {e}"
    return {
        "kysymys": tapaus["kysymys"],
        "vastaus": vastaus,
        "virhe": virhe,
        "kierrokset": mittari.kierrokset,
        "tokenit": {"syote": mittari.syote_tokenit, "tuloste": mittari.tuloste_tokenit},
        "kesto_s": round(time.monotonic() - alku, 1),
        "kutsut": kutsut_historiasta(agentti.viestit),
    }


def _git_versio() -> str:
    try:
        tiiviste = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        muutoksia = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        return tiiviste + ("-muokattu" if muutoksia else "")
    except (OSError, subprocess.CalledProcessError):
        return "tuntematon"


def yhteenveto(tulokset: list[dict]) -> dict:
    n = len(tulokset)
    onnistuneet = sum(t["onnistui"] for t in tulokset)
    jaljet = [t["jalki"] for t in tulokset]
    kutsuja = sum(len(j["kutsut"]) for j in jaljet)
    return {
        "tapauksia": n,
        "onnistui": onnistuneet,
        "onnistumisprosentti": round(100 * onnistuneet / n, 1) if n else 0.0,
        "kierroksia_keskimaarin": round(sum(j["kierrokset"] for j in jaljet) / n, 2) if n else 0.0,
        "tyokalukutsuja_yhteensa": kutsuja,
        "tyokalukutsuja_keskimaarin": round(kutsuja / n, 2) if n else 0.0,
        "syote_tokenit": sum(j["tokenit"]["syote"] for j in jaljet),
        "tuloste_tokenit": sum(j["tokenit"]["tuloste"] for j in jaljet),
    }


def tulosta_yhteenveto(y: dict, tulokset: list[dict]) -> None:
    print("\n" + "=" * 60)
    print(f"Onnistui {y['onnistui']}/{y['tapauksia']} ({y['onnistumisprosentti']} %)")
    print(f"Kierroksia keskimäärin:     {y['kierroksia_keskimaarin']}")
    print(f"Työkalukutsuja:             {y['tyokalukutsuja_yhteensa']} (keskim. {y['tyokalukutsuja_keskimaarin']})")
    print(f"Tokenit (syöte / tuloste):  {y['syote_tokenit']} / {y['tuloste_tokenit']}")
    epaonnistuneet = [t for t in tulokset if not t["onnistui"]]
    if epaonnistuneet:
        print("\nEpäonnistuneet:")
        for t in epaonnistuneet:
            print(f"  ✗ {t['id']}")
            if t["jalki"]["virhe"]:
                print(f"      - {t['jalki']['virhe']}")
            for nimi, syy in t["tarkistukset"].items():
                if syy:
                    print(f"      - {nimi}: {syy}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Aja grocery-agentin evalit (kutsuu oikeaa API:a).")
    parser.add_argument("--vain", nargs="+", metavar="ID", help="aja vain nämä tapaukset")
    parser.add_argument("--malli", default=config.MODEL, help="malli (oletus: MODEL .env-tiedostosta)")
    parser.add_argument("-v", "--verbose", action="store_true", help="näytä agentin työkalukutsut")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="  [%(message)s]")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    tapaukset = json.loads(TAPAUKSET.read_text(encoding="utf-8"))
    validoi_tapaukset(tapaukset)
    if args.vain:
        tuntemattomat = set(args.vain) - {t["id"] for t in tapaukset}
        if tuntemattomat:
            sys.exit(f"Tuntemattomat tapaukset: {', '.join(sorted(tuntemattomat))}")
        tapaukset = [t for t in tapaukset if t["id"] in args.vain]

    if not args.malli:
        sys.exit("Malli puuttuu. Aseta MODEL .env-tiedostoon tai anna --malli.")
    if not config.OPENAI_API_KEY:
        sys.exit("API-avain puuttuu. Aseta OPENAI_API_KEY .env-tiedostoon.")
    if not config.DATABASE_PATH.exists():
        sys.exit(f"Tietokantaa ei löydy: {config.DATABASE_PATH}\nAja ensin: python scripts/seed.py")

    client = openai.OpenAI(api_key=config.OPENAI_API_KEY)
    conn = db.connect()
    kanta = Tuotetiedot(conn)
    aloitettu = datetime.now()
    print(f"Ajetaan {len(tapaukset)} tapausta mallilla {args.malli}\n")

    tulokset = []
    try:
        for i, tapaus in enumerate(tapaukset, 1):
            jalki = aja_tapaus(tapaus, client, conn, args.malli)
            tarkistukset = tarkista(tapaus, jalki, kanta)
            onnistui = jalki["virhe"] is None and not any(tarkistukset.values())
            tulokset.append({"id": tapaus["id"], "onnistui": onnistui, "tarkistukset": tarkistukset, "jalki": jalki})
            merkki = "✓" if onnistui else "✗"
            print(
                f"{merkki} [{i:2}/{len(tapaukset)}] {tapaus['id']:<32} "
                f"{jalki['kierrokset']} kierr., {len(jalki['kutsut'])} kutsua, "
                f"{jalki['tokenit']['syote'] + jalki['tokenit']['tuloste']} tok."
            )
    except openai.AuthenticationError:
        sys.exit("API-avain on virheellinen. Tarkista OPENAI_API_KEY.")
    except KeyboardInterrupt:
        print("\nKeskeytetty, tallennetaan tähän asti ajetut tapaukset.")
    finally:
        conn.close()

    if not tulokset:
        return
    y = yhteenveto(tulokset)
    tulosta_yhteenveto(y, tulokset)

    TULOKSET.mkdir(exist_ok=True)
    polku = TULOKSET / f"{aloitettu:%Y-%m-%d_%H%M%S}.json"
    polku.write_text(
        json.dumps(
            {
                "aloitettu": aloitettu.isoformat(timespec="seconds"),
                "malli": args.malli,
                "git": _git_versio(),
                "yhteenveto": y,
                "tapaukset": tulokset,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nTulokset tallennettu: {polku.relative_to(config.PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
