"""Ajaa eval-tapaukset oikeaa agenttia ja OpenAI API:a vasten. MAKSAA RAHAA.

Ei ole osa pytestiä. Käyttö projektin juuresta:
    .venv/bin/python -m evals.aja                      # kaikki tapaukset
    .venv/bin/python -m evals.aja --vain banaanin_hinta mahdoton_budjetti
    .venv/bin/python -m evals.aja --malli <malli>      # vertaa malleja
    .venv/bin/python -m evals.aja --kertaa 3           # 3 ajoa + keskiarvo ja vaihtelu
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
    vastaus, virhe, api_virhe = None, None, None
    try:
        vastaus = agentti.kysy(tapaus["kysymys"])
    except openai.APIError as e:
        # Pyyntö ei mennyt läpi (väärä parametri, avain, verkko, käyttöraja). Tämä kertoo
        # asetuksista, ei agentin käytöksestä, joten tapausta ei arvioida.
        api_virhe = f"{type(e).__name__}: {e}"
    except Exception as e:
        # Kierrosraja tai katkennut vastaus: agentin käytöstä, tapaus epäonnistuu.
        # Huom: agentti palauttaa historian virheen jälkeen, joten kutsut puuttuvat jäljestä.
        virhe = f"{type(e).__name__}: {e}"
    return {
        "kysymys": tapaus["kysymys"],
        "vastaus": vastaus,
        "virhe": virhe,
        "api_virhe": api_virhe,
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


def tila(tulos: dict) -> str:
    """onnistui / epaonnistui / virhe. Vanhoissa tulostiedostoissa on vain onnistui-kenttä."""
    return tulos.get("tila") or ("onnistui" if tulos["onnistui"] else "epaonnistui")


def _prosentti(p) -> str:
    return "–" if p is None else f"{p} %"


def yhteenveto(tulokset: list[dict]) -> dict:
    """API-virheet eivät ole mukana prosentissa eivätkä keskiarvoissa, tokeneissa ovat."""
    arvioidut = [t for t in tulokset if tila(t) != "virhe"]
    n = len(arvioidut)
    onnistuneet = sum(tila(t) == "onnistui" for t in arvioidut)
    jaljet = [t["jalki"] for t in arvioidut]
    kutsuja = sum(len(j["kutsut"]) for j in jaljet)
    return {
        "tapauksia": len(tulokset),
        "arvioitu": n,
        "onnistui": onnistuneet,
        "api_virheita": len(tulokset) - n,
        "onnistumisprosentti": round(100 * onnistuneet / n, 1) if n else None,
        "kierroksia_keskimaarin": round(sum(j["kierrokset"] for j in jaljet) / n, 2) if n else 0.0,
        "tyokalukutsuja_yhteensa": kutsuja,
        "tyokalukutsuja_keskimaarin": round(kutsuja / n, 2) if n else 0.0,
        "syote_tokenit": sum(t["jalki"]["tokenit"]["syote"] for t in tulokset),
        "tuloste_tokenit": sum(t["jalki"]["tokenit"]["tuloste"] for t in tulokset),
    }


def tulosta_yhteenveto(y: dict, tulokset: list[dict]) -> None:
    print("\n" + "=" * 60)
    print(f"Onnistui {y['onnistui']}/{y['arvioitu']} arvioidusta ({_prosentti(y['onnistumisprosentti'])})")
    if y["api_virheita"]:
        print(f"API-virheitä:               {y['api_virheita']} (eivät mukana prosentissa)")
    print(f"Kierroksia keskimäärin:     {y['kierroksia_keskimaarin']}")
    print(f"Työkalukutsuja:             {y['tyokalukutsuja_yhteensa']} (keskim. {y['tyokalukutsuja_keskimaarin']})")
    print(f"Tokenit (syöte / tuloste):  {y['syote_tokenit']} / {y['tuloste_tokenit']}")
    epaonnistuneet = [t for t in tulokset if tila(t) == "epaonnistui"]
    if epaonnistuneet:
        print("\nEpäonnistuneet:")
        for t in epaonnistuneet:
            print(f"  ✗ {t['id']}")
            if t["jalki"]["virhe"]:
                print(f"      - {t['jalki']['virhe']}")
            for nimi, syy in t["tarkistukset"].items():
                if syy:
                    print(f"      - {nimi}: {syy}")
    # Sama virhe toistuu yleensä joka tapauksessa: ryhmitellään viestin mukaan.
    virheet: dict[str, list[str]] = {}
    for t in tulokset:
        if tila(t) == "virhe":
            virheet.setdefault(t["jalki"]["api_virhe"], []).append(t["id"])
    if virheet:
        print("\nAPI-virheet (ei arvioitu):")
        for viesti, idt in virheet.items():
            print(f"  ! {len(idt)} tapausta: {viesti}")


def ajojen_vertailu(ajot: list[list[dict]]) -> dict:
    """Vertaa saman setin useita ajoja: malli ei ole deterministinen, joten yksi ajo ei riitä.

    Palauttaa onnistumisprosentit, niiden keskiarvon ja vaihteluvälin sekä tapaukset,
    jotka epäonnistuivat joka kerta (todennäköisesti oikea vika) tai vaihtelivat
    (epävakaa käytös tai liian tiukka tarkistus). API-virheet lasketaan erikseen.
    """
    prosentit = [yhteenveto(ajo)["onnistumisprosentti"] for ajo in ajot]
    arvioidut_prosentit = [p for p in prosentit if p is not None]
    onnistui: dict[str, int] = {}
    arvioitu: dict[str, int] = {}
    virheita: dict[str, int] = {}
    for ajo in ajot:
        for t in ajo:
            i = t["id"]
            if tila(t) == "virhe":
                virheita[i] = virheita.get(i, 0) + 1
                continue
            arvioitu[i] = arvioitu.get(i, 0) + 1
            onnistui[i] = onnistui.get(i, 0) + (tila(t) == "onnistui")
    return {
        "ajoja": len(ajot),
        "prosentit": prosentit,
        "keskiarvo": round(sum(arvioidut_prosentit) / len(arvioidut_prosentit), 1) if arvioidut_prosentit else None,
        "pienin": min(arvioidut_prosentit, default=None),
        "suurin": max(arvioidut_prosentit, default=None),
        "aina_epaonnistuneet": [i for i, n in arvioitu.items() if onnistui[i] == 0],
        "vaihtelevat": {i: f"{onnistui[i]}/{n}" for i, n in arvioitu.items() if 0 < onnistui[i] < n},
        "api_virheita": {i: f"{n}/{len(ajot)}" for i, n in virheita.items()},
    }


def tulosta_vertailu(v: dict) -> None:
    print("\n" + "#" * 60)
    print(f"{v['ajoja']} ajoa: " + ", ".join(_prosentti(p) for p in v["prosentit"]))
    if v["keskiarvo"] is None:
        print("Yhtään tapausta ei voitu arvioida (kaikki API-virheitä).")
    else:
        print(f"Keskiarvo {v['keskiarvo']} %, vaihteluväli {v['pienin']}–{v['suurin']} %")
    print("\nEpäonnistui joka kerta:")
    for i in v["aina_epaonnistuneet"] or ["(ei yhtään)"]:
        print(f"  ✗ {i}")
    print("\nVaihteli ajojen välillä (onnistui / arvioitu):")
    for i, osuus in v["vaihtelevat"].items() or [("(ei yhtään)", "")]:
        print(f"  ~ {i} {osuus}")
    if v["api_virheita"]:
        print("\nAPI-virheitä (virhe / ajoja):")
        for i, osuus in v["api_virheita"].items():
            print(f"  ! {i} {osuus}")


# Jos näin monta ensimmäistä tapausta kaatuu samaan API-virheeseen, vika on asetuksissa.
KESKEYTYSRAJA = 3


class ApiVirheKeskeytys(RuntimeError):
    """Sama API-virhe toistuu alusta asti: jatkaminen vain kuluttaisi aikaa ja rahaa."""

    def __init__(self, viesti: str, tulokset: list[dict]):
        super().__init__(viesti)
        self.tulokset = tulokset


def aja_setti(tapaukset, client, conn, kanta, malli) -> list[dict]:
    """Ajaa tapaukset kerran. KeyboardInterrupt ja ApiVirheKeskeytys jättävät keskeneräisen ajon talteen."""
    tulokset = []
    try:
        for i, tapaus in enumerate(tapaukset, 1):
            jalki = aja_tapaus(tapaus, client, conn, malli)
            if jalki["api_virhe"]:
                # Ei tarkistuksia: vastausta ei ole, joten niiden tulos olisi merkityksetön.
                tarkistukset, tulos_tila = {}, "virhe"
            else:
                tarkistukset = tarkista(tapaus, jalki, kanta)
                ok = jalki["virhe"] is None and not any(tarkistukset.values())
                tulos_tila = "onnistui" if ok else "epaonnistui"
            tulokset.append(
                {
                    "id": tapaus["id"],
                    "tila": tulos_tila,
                    "onnistui": tulos_tila == "onnistui",
                    "tarkistukset": tarkistukset,
                    "jalki": jalki,
                }
            )
            merkki = {"onnistui": "✓", "epaonnistui": "✗", "virhe": "!"}[tulos_tila]
            print(
                f"{merkki} [{i:2}/{len(tapaukset)}] {tapaus['id']:<32} "
                f"{jalki['kierrokset']} kierr., {len(jalki['kutsut'])} kutsua, "
                f"{jalki['tokenit']['syote'] + jalki['tokenit']['tuloste']} tok."
            )
            if i == KESKEYTYSRAJA:
                viestit = {t["jalki"]["api_virhe"] for t in tulokset}
                if len(viestit) == 1 and None not in viestit:
                    raise ApiVirheKeskeytys(viestit.pop(), tulokset)
    except KeyboardInterrupt as e:
        e.tulokset = tulokset
        raise
    return tulokset


def tallenna(tulokset: list[dict], aloitettu: datetime, malli: str) -> Path:
    TULOKSET.mkdir(exist_ok=True)
    polku = TULOKSET / f"{aloitettu:%Y-%m-%d_%H%M%S}.json"
    n = 2
    while polku.exists():  # lyhyet --vain-ajot voivat alkaa samalla sekunnilla
        polku = TULOKSET / f"{aloitettu:%Y-%m-%d_%H%M%S}_{n}.json"
        n += 1
    polku.write_text(
        json.dumps(
            {
                "aloitettu": aloitettu.isoformat(timespec="seconds"),
                "malli": malli,
                "reasoning_effort": config.REASONING_EFFORT or None,
                "git": _git_versio(),
                "yhteenveto": yhteenveto(tulokset),
                "tapaukset": tulokset,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return polku


def main() -> None:
    parser = argparse.ArgumentParser(description="Aja grocery-agentin evalit (kutsuu oikeaa API:a).")
    parser.add_argument("--vain", nargs="+", metavar="ID", help="aja vain nämä tapaukset")
    parser.add_argument("--malli", default=config.MODEL, help="malli (oletus: MODEL .env-tiedostosta)")
    parser.add_argument("--kertaa", type=int, default=1, metavar="N", help="aja setti N kertaa ja vertaa (oletus 1)")
    parser.add_argument("-v", "--verbose", action="store_true", help="näytä agentin työkalukutsut")
    args = parser.parse_args()
    if args.kertaa < 1:
        parser.error("--kertaa pitää olla vähintään 1")

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
    ajot: list[list[dict]] = []
    try:
        for n in range(1, args.kertaa + 1):
            aloitettu = datetime.now()
            otsikko = f" (ajo {n}/{args.kertaa})" if args.kertaa > 1 else ""
            effort = f", reasoning_effort={config.REASONING_EFFORT}" if config.REASONING_EFFORT else ""
            print(f"\nAjetaan {len(tapaukset)} tapausta mallilla {args.malli}{effort}{otsikko}\n")
            try:
                tulokset = aja_setti(tapaukset, client, conn, kanta, args.malli)
            except ApiVirheKeskeytys as e:
                print(f"\nKeskeytetään: {KESKEYTYSRAJA} ensimmäistä tapausta kaatui samaan API-virheeseen:")
                print(f"  {e}")
                print("Korjaa asetukset (.env: MODEL, REASONING_EFFORT, OPENAI_API_KEY) ja aja uudelleen.")
                print(f"\nTulokset tallennettu: {tallenna(e.tulokset, aloitettu, args.malli).relative_to(config.PROJECT_ROOT)}")
                break
            except KeyboardInterrupt as e:
                # Keskeneräinen ajo tallennetaan, mutta sitä ei oteta vertailuun.
                if e.tulokset:
                    print("\nKeskeytetty, tallennetaan tähän asti ajetut tapaukset.")
                    tulosta_yhteenveto(yhteenveto(e.tulokset), e.tulokset)
                    print(f"\nTulokset tallennettu: {tallenna(e.tulokset, aloitettu, args.malli).relative_to(config.PROJECT_ROOT)}")
                break
            ajot.append(tulokset)
            tulosta_yhteenveto(yhteenveto(tulokset), tulokset)
            print(f"\nTulokset tallennettu: {tallenna(tulokset, aloitettu, args.malli).relative_to(config.PROJECT_ROOT)}")
    finally:
        conn.close()

    if len(ajot) > 1:
        tulosta_vertailu(ajojen_vertailu(ajot))


if __name__ == "__main__":
    main()
