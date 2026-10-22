"""Ruokaostos-agentti: agenttisilmukka Anthropic SDK:lla ja komentorivikäyttöliittymä.

Käyttö:
    python -m ruokaostos.agent
"""

import json
import logging
import sqlite3
import sys

import anthropic

from ruokaostos import config, db, tools

log = logging.getLogger(__name__)

# Yhden käyttäjän viestin aikana sallitut mallikutsut. Normaali kysymys vaatii
# 2–5 kierrosta; raja estää loputtoman työkalusilmukan ja karkaavat kulut.
MAX_KIERROKSET = 15
MAX_TOKENS = 16000

JARJESTELMAPROMPTI = """\
Olet suomalaisen ruokakaupan ostosavustaja. Autat käyttäjää kokoamaan ostoslistan \
kaupan valikoimasta ja kerrot, mitä lista maksaa.

Tärkeimmät säännöt:
- Älä koskaan keksi tuotteita, hintoja tai ruokavaliotietoja. Kaikki tieto \
valikoimasta tulee työkaluista. Jos et ole hakenut tuotetta työkalulla, et tiedä \
onko sitä myynnissä.
- Laske hinnat aina laske_hinta-työkalulla, älä päässälaskien.
- Jos sopivaa tuotetta ei löydy, sano se suoraan ja ehdota lähintä löytynyttä \
vaihtoehtoa. Älä väitä tuotteen sopivan ruokavalioon, jos merkintää ei ole.
- Ruokavaliomerkinnän puuttuminen tarkoittaa, ettei tuotteen sopivuutta ole \
varmistettu, ei sitä, että tuote olisi sopimaton. Kerro tämä käyttäjälle, kun \
asialla on merkitystä (esim. keliakia tai allergia).

Työskentely:
- Jos käyttäjällä on ruokavaliorajoitteita, käytä niitä jokaisessa haussa.
- Hae tuotteita lyhyillä perusmuotoisilla hakusanoilla (esim. "maito", "juusto", \
"peruna"). Jos haku ei tuota tuloksia, kokeile toista sanaa tai selaa kategoriaa \
tyhjällä hakusanalla.
- Voit tehdä useita hakuja kerralla, kun tarvitset monta tuotetta.
- Kg- ja l-hintaisissa tuotteissa määrä on kiloina tai litroina; arvioi järkevä \
määrä ja kerro arviosi käyttäjälle.

Vastaukset:
- Vastaa suomeksi, ytimekkäästi.
- Työkalut käyttävät senttejä. Näytä hinnat käyttäjälle euroina suomalaisittain, \
esim. 3,49 €.
- Kun esität ostoslistan, listaa jokainen rivi (tuote, määrä, rivihinta) ja \
yhteissumma laske_hinta-työkalun tuloksesta.
"""

TYOKALUT = [
    {
        "name": "hae_tuotteet",
        "description": (
            "Hakee kaupan tuotteita nimen osalla. Palauttaa tuotteen id:n, nimen, "
            "kategorian, hinnan sentteinä, hintayksikön (kpl, kg tai l) ja "
            "ruokavaliomerkinnät. Tulokset ovat halvimmasta kalleimpaan. Tuotteen "
            "täytyy täyttää kaikki annetut ruokavaliot. Tyhjä hakusana listaa kaikki "
            "suodattimet täyttävät tuotteet. Käytä palautettua id:tä laske_hinta-työkalussa."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "hakusana": {
                    "type": "string",
                    "description": "Osa tuotteen nimestä, esim. 'maito'. Kirjainkoolla ei ole väliä.",
                },
                "kategoria": {
                    "type": "string",
                    "description": "Rajaa kategoriaan. Nimet saat listaa_kategoriat-työkalulla.",
                },
                "ruokavaliot": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["gluteeniton", "laktoositon", "vegaaninen"]},
                    "description": "Ruokavaliot, jotka tuotteen pitää täyttää (kaikki).",
                },
                "max_hinta_snt": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Enimmäishinta sentteinä yksikköä kohden.",
                },
                "raja": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Tulosten enimmäismäärä (oletus 50).",
                },
            },
            "required": ["hakusana"],
            "additionalProperties": False,
        },
    },
    {
        "name": "listaa_kategoriat",
        "description": "Listaa kaupan tuotekategoriat ja niiden tuotemäärät.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "listaa_ruokavaliot",
        "description": (
            "Listaa tuetut ruokavaliot: koodi (käytetään hae_tuotteet-työkalussa), "
            "nimi ja merkittyjen tuotteiden määrä."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "laske_hinta",
        "description": (
            "Laskee ostoslistan hinnan. Palauttaa rivikohtaiset hinnat ja yhteissumman "
            "sentteinä. Määrä annetaan tuotteen yksikössä: kpl-tuotteille kokonaisluku, "
            "kg- ja l-tuotteille desimaaliluku sallittu (esim. 0.5 kg)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ostoslista": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tuote_id": {"type": "integer", "description": "hae_tuotteet-työkalun id"},
                            "maara": {"type": "number", "exclusiveMinimum": 0},
                        },
                        "required": ["tuote_id", "maara"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["ostoslista"],
            "additionalProperties": False,
        },
    },
]

_FUNKTIOT = {
    "hae_tuotteet": tools.hae_tuotteet,
    "listaa_kategoriat": tools.listaa_kategoriat,
    "listaa_ruokavaliot": tools.listaa_ruokavaliot,
    "laske_hinta": tools.laske_hinta,
}


def aja_tyokalu(nimi: str, syote: dict, conn: sqlite3.Connection) -> dict:
    """Ajaa työkalun ja palauttaa valmiin tool_result-sisällön (ilman tool_use_id:tä)."""
    log.info("työkalu %s(%s)", nimi, json.dumps(syote, ensure_ascii=False))
    funktio = _FUNKTIOT.get(nimi)
    if funktio is None:
        return {"content": f"Tuntematon työkalu: {nimi}", "is_error": True}
    try:
        tulos = funktio(**syote, conn=conn)
    except tools.TyokaluVirhe as e:
        return {"content": str(e), "is_error": True}
    except TypeError as e:
        # Malli antoi parametrin, jota funktio ei tunne.
        return {"content": f"Virheelliset parametrit: {e}", "is_error": True}
    except Exception:
        # Ohjelmointivirhe: kirjataan, mutta keskustelu jatkuu. Jokaiselle
        # tool_use-lohkolle on pakko palauttaa tulos tai historia rikkoutuu.
        log.exception("työkalu %s kaatui", nimi)
        return {"content": "Sisäinen virhe työkalussa. Kerro käyttäjälle, ettei tietoa saatu.", "is_error": True}
    return {"content": json.dumps(tulos, ensure_ascii=False)}


class KierrosrajaYlittyi(RuntimeError):
    pass


class Agentti:
    """Keskustelu, joka muistaa historian viestien välillä."""

    def __init__(self, client: anthropic.Anthropic, conn: sqlite3.Connection, model: str = config.ANTHROPIC_MODEL):
        self.client = client
        self.conn = conn
        self.model = model
        self.viestit: list = []

    def kysy(self, teksti: str) -> str:
        """Lähettää käyttäjän viestin ja ajaa silmukkaa, kunnes malli antaa vastauksen."""
        alku = len(self.viestit)
        self.viestit.append({"role": "user", "content": teksti})
        try:
            return self._silmukka()
        except Exception:
            # Palautetaan historia tilaan ennen viestiä, jotta seuraava kysymys
            # ei peri keskeneräistä työkalukierrosta.
            del self.viestit[alku:]
            raise

    def _silmukka(self) -> str:
        for _ in range(MAX_KIERROKSET):
            vastaus = self.client.beta.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=JARJESTELMAPROMPTI,
                tools=TYOKALUT,
                messages=self.viestit,
                output_config={"effort": "medium"},
                # Siirtyvä välimuistipiste: järjestelmäprompti, työkalut ja aiempi
                # historia luetaan välimuistista joka kierroksella.
                cache_control={"type": "ephemeral"},
                # Jos turvallisuusluokitin hylkää pyynnön, API ajaa sen
                # suositellulla varamallilla saman kutsun sisällä.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            log.debug("stop_reason=%s usage=%s", vastaus.stop_reason, vastaus.usage)
            # Koko content takaisin muuttamattomana (myös thinking-lohkot).
            self.viestit.append({"role": "assistant", "content": vastaus.content})

            if vastaus.stop_reason == "tool_use":
                tulokset = [
                    {"type": "tool_result", "tool_use_id": b.id, **aja_tyokalu(b.name, b.input, self.conn)}
                    for b in vastaus.content
                    if b.type == "tool_use"
                ]
                # Kaikki rinnakkaisten kutsujen tulokset samassa viestissä.
                self.viestit.append({"role": "user", "content": tulokset})
                continue
            if vastaus.stop_reason == "pause_turn":
                continue
            if vastaus.stop_reason == "refusal":
                return "Pahoittelen, en voi auttaa tämän pyynnön kanssa."
            if vastaus.stop_reason == "max_tokens":
                raise RuntimeError("Vastaus katkesi max_tokens-rajaan")
            return "\n".join(b.text for b in vastaus.content if b.type == "text").strip()

        raise KierrosrajaYlittyi(f"Agentti ei saanut vastausta valmiiksi {MAX_KIERROKSET} kierroksessa")


def main() -> None:
    logging.basicConfig(level=config.LOG_LEVEL, format="  [%(message)s]", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)

    if not config.DATABASE_PATH.exists():
        sys.exit(f"Tietokantaa ei löydy: {config.DATABASE_PATH}\nAja ensin: python scripts/seed.py")

    # api_key=None antaa SDK:n etsiä tunnukset itse (ANTHROPIC_API_KEY, ant auth login -profiili).
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None)
    conn = db.connect()
    agentti = Agentti(client, conn)

    print("Ruokaostos-agentti. Kerro mitä tarvitset. Lopeta: 'lopeta' tai Ctrl-D.\n")
    try:
        while True:
            try:
                teksti = input("> ").strip()
            except EOFError:
                break
            if teksti.lower() in {"lopeta", "exit", "quit"}:
                break
            if not teksti:
                continue
            try:
                print(f"\n{agentti.kysy(teksti)}\n")
            except KierrosrajaYlittyi as e:
                print(f"\n{e}. Kokeile tarkentaa pyyntöä.\n")
            except anthropic.AuthenticationError:
                sys.exit("API-avain puuttuu tai on virheellinen. Aseta ANTHROPIC_API_KEY .env-tiedostoon.")
            except anthropic.RateLimitError:
                print("\nAPI:n käyttöraja täyttyi, odota hetki ja yritä uudelleen.\n")
            except anthropic.APIConnectionError:
                print("\nYhteys API:in epäonnistui. Tarkista verkkoyhteys.\n")
            except anthropic.APIStatusError as e:
                print(f"\nAPI-virhe {e.status_code}: {e.message}\n")
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()
    print("Hei hei!")


if __name__ == "__main__":
    main()
