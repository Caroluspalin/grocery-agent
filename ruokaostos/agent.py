"""Ruokaostos-agentti: agenttisilmukka OpenAI:n Chat Completions -rajapinnalla ja komentorivikäyttöliittymä.

Käyttö:
    python -m ruokaostos.agent
"""

import json
import logging
import sqlite3
import sys

import openai

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
        "type": "function",
        "function": {
            "name": "hae_tuotteet",
            "description": (
                "Hakee kaupan tuotteita nimen osalla. Palauttaa tuotteen id:n, nimen, "
                "kategorian, hinnan sentteinä, hintayksikön (kpl, kg tai l) ja "
                "ruokavaliomerkinnät. Tulokset ovat halvimmasta kalleimpaan. Tuotteen "
                "täytyy täyttää kaikki annetut ruokavaliot. Tyhjä hakusana listaa kaikki "
                "suodattimet täyttävät tuotteet. Käytä palautettua id:tä laske_hinta-työkalussa."
            ),
            "parameters": {
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
    },
    {
        "type": "function",
        "function": {
            "name": "listaa_kategoriat",
            "description": "Listaa kaupan tuotekategoriat ja niiden tuotemäärät.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listaa_ruokavaliot",
            "description": (
                "Listaa tuetut ruokavaliot: koodi (käytetään hae_tuotteet-työkalussa), "
                "nimi ja merkittyjen tuotteiden määrä."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "laske_hinta",
            "description": (
                "Laskee ostoslistan hinnan. Palauttaa rivikohtaiset hinnat ja yhteissumman "
                "sentteinä. Määrä annetaan tuotteen yksikössä: kpl-tuotteille kokonaisluku, "
                "kg- ja l-tuotteille desimaaliluku sallittu (esim. 0.5 kg)."
            ),
            "parameters": {
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
    },
]

_FUNKTIOT = {
    "hae_tuotteet": tools.hae_tuotteet,
    "listaa_kategoriat": tools.listaa_kategoriat,
    "listaa_ruokavaliot": tools.listaa_ruokavaliot,
    "laske_hinta": tools.laske_hinta,
}

# OpenAI:n tool-viestissä ei ole is_error-kenttää, joten virhe merkitään tekstiin.
VIRHE_ETULIITE = "VIRHE: "


def _pura_argumentit(argumentit: str) -> dict:
    """OpenAI antaa argumentit JSON-merkkijonona; malli voi tuottaa rikkinäistä JSONia."""
    if not argumentit:
        return {}
    try:
        syote = json.loads(argumentit)
    except json.JSONDecodeError as e:
        raise tools.TyokaluVirhe(
            f"Argumentit eivät ole kelvollista JSONia ({e.msg}). Lähetä argumentit JSON-objektina."
        ) from e
    if not isinstance(syote, dict):
        raise tools.TyokaluVirhe("Argumenttien pitää olla JSON-objekti, esim. {\"hakusana\": \"maito\"}.")
    return syote


def aja_tyokalu(nimi: str, argumentit: str, conn: sqlite3.Connection) -> str:
    """Ajaa työkalun ja palauttaa tool-viestin sisällön tekstinä (JSON tai virheteksti)."""
    log.info("työkalu %s(%s)", nimi, argumentit)
    funktio = _FUNKTIOT.get(nimi)
    if funktio is None:
        return f"{VIRHE_ETULIITE}Tuntematon työkalu: {nimi}"
    try:
        tulos = funktio(**_pura_argumentit(argumentit), conn=conn)
    except tools.TyokaluVirhe as e:
        return f"{VIRHE_ETULIITE}{e}"
    except TypeError as e:
        # Malli antoi parametrin, jota funktio ei tunne.
        return f"{VIRHE_ETULIITE}Virheelliset parametrit: {e}"
    except Exception:
        # Ohjelmointivirhe: kirjataan, mutta keskustelu jatkuu. Jokaiselle
        # tool_calls-kutsulle on pakko palauttaa tool-viesti tai API hylkää historian.
        log.exception("työkalu %s kaatui", nimi)
        return f"{VIRHE_ETULIITE}Sisäinen virhe työkalussa. Kerro käyttäjälle, ettei tietoa saatu."
    return json.dumps(tulos, ensure_ascii=False)


class KierrosrajaYlittyi(RuntimeError):
    pass


class Agentti:
    """Keskustelu, joka muistaa historian viestien välillä."""

    def __init__(self, client: openai.OpenAI, conn: sqlite3.Connection, model: str = config.MODEL):
        self.client = client
        self.conn = conn
        self.model = model
        # Järjestelmäprompti on historian ensimmäinen viesti, ei erillinen parametri.
        self.viestit: list = [{"role": "system", "content": JARJESTELMAPROMPTI}]

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
            vastaus = self.client.chat.completions.create(
                model=self.model,
                messages=self.viestit,
                tools=TYOKALUT,
                max_completion_tokens=MAX_TOKENS,
            )
            valinta = vastaus.choices[0]
            viesti = valinta.message
            log.debug("finish_reason=%s usage=%s", valinta.finish_reason, vastaus.usage)

            # Assistentin viesti historiaan tavallisena dictinä, jotta historia
            # on helppo tulostaa, tallentaa ja testata.
            assistentti = {"role": "assistant", "content": viesti.content}
            if viesti.tool_calls:
                assistentti["tool_calls"] = [
                    {
                        "id": k.id,
                        "type": "function",
                        "function": {"name": k.function.name, "arguments": k.function.arguments},
                    }
                    for k in viesti.tool_calls
                ]
            self.viestit.append(assistentti)

            if viesti.tool_calls:
                # Jokainen kutsu saa oman tool-viestinsä samalla id:llä.
                for k in viesti.tool_calls:
                    self.viestit.append(
                        {
                            "role": "tool",
                            "tool_call_id": k.id,
                            "content": aja_tyokalu(k.function.name, k.function.arguments, self.conn),
                        }
                    )
                continue
            if getattr(viesti, "refusal", None) or valinta.finish_reason == "content_filter":
                return "Pahoittelen, en voi auttaa tämän pyynnön kanssa."
            if valinta.finish_reason == "length":
                raise RuntimeError("Vastaus katkesi max_completion_tokens-rajaan")
            return (viesti.content or "").strip()

        raise KierrosrajaYlittyi(f"Agentti ei saanut vastausta valmiiksi {MAX_KIERROKSET} kierroksessa")


def main() -> None:
    logging.basicConfig(level=config.LOG_LEVEL, format="  [%(message)s]", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if not config.DATABASE_PATH.exists():
        sys.exit(f"Tietokantaa ei löydy: {config.DATABASE_PATH}\nAja ensin: python scripts/seed.py")
    if not config.MODEL:
        sys.exit("Malli puuttuu. Aseta MODEL .env-tiedostoon (katso .env.example).")
    if not config.OPENAI_API_KEY:
        sys.exit("API-avain puuttuu. Aseta OPENAI_API_KEY .env-tiedostoon.")

    client = openai.OpenAI(api_key=config.OPENAI_API_KEY)
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
            except openai.AuthenticationError:
                sys.exit("API-avain on virheellinen. Tarkista OPENAI_API_KEY .env-tiedostosta.")
            except openai.RateLimitError:
                print("\nAPI:n käyttöraja tai saldo täyttyi, odota hetki tai tarkista laskutus.\n")
            except openai.APIConnectionError:
                print("\nYhteys API:in epäonnistui. Tarkista verkkoyhteys.\n")
            except openai.APIStatusError as e:
                print(f"\nAPI-virhe {e.status_code}: {e.message}\n")
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()
    print("Hei hei!")


if __name__ == "__main__":
    main()
