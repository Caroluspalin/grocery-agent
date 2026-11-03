"""Eval-tapausten tarkistukset. Puhdasta koodia, ei LLM-tuomaria.

Jokainen tarkistus saa
- `arvo`: tapauksen ehdon arvo tapaukset.json-tiedostosta,
- `jalki`: yhden ajon jälki (kysymys, vastaus, työkalukutsut tuloksineen),
- `kanta`: tuotetiedot tietokannasta (kategoria ja ruokavaliot id:n perusteella),
ja palauttaa None, jos ehto täyttyy, tai syyn tekstinä, jos ei.

"Lopullinen ostoslista" on viimeisen onnistuneen laske_hinta-kutsun tulos.
Ostoslistaa koskevat rajat (budjetti, ruokavaliot) eivät kaada tapausta, jos listaa
ei tehty lainkaan; listan olemassaolo vaaditaan erikseen ehdolla laske_hinta_kutsuttu.
"""

import json
import re
import sqlite3
from collections.abc import Callable

RUOKAVALIOSANAT = ("gluteen", "laktoos", "vegaan", "maidoton")

# "3,49 €", "10 €", "10 euroa", "3.49€"
_EURO = re.compile(r"(\d{1,6})(?:[,.](\d{1,2}))?\s?(?:€|euroa?\b|eur\b)", re.IGNORECASE)


# --- apufunktiot ----------------------------------------------------------


def euromaarat_snt(teksti: str) -> set[int]:
    """Poimii tekstin euromäärät sentteinä."""
    tulos = set()
    for eurot, sentit in _EURO.findall(teksti or ""):
        tulos.add(int(eurot) * 100 + (int(sentit.ljust(2, "0")) if sentit else 0))
    return tulos


def _onnistuneet(jalki: dict, nimi: str) -> list[dict]:
    return [k for k in jalki["kutsut"] if k["nimi"] == nimi and not k["virhe"]]


def _tulos(kutsu: dict):
    try:
        return json.loads(kutsu["tulos"])
    except (json.JSONDecodeError, TypeError):
        return None


def ostoslista(jalki: dict) -> dict | None:
    laskut = _onnistuneet(jalki, "laske_hinta")
    return _tulos(laskut[-1]) if laskut else None


def _hakuargumentit(jalki: dict) -> list[dict]:
    return [k["argumentit"] or {} for k in jalki["kutsut"] if k["nimi"] == "hae_tuotteet"]


def _vastaus(jalki: dict) -> str:
    return (jalki["vastaus"] or "").casefold()


class Tuotetiedot:
    """Tuotteiden kategoria ja ruokavaliot suoraan kannasta, ei agentin kertomana."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def hae(self, tuote_id: int) -> dict | None:
        rivi = self.conn.execute(
            """
            SELECT t.nimi, k.nimi AS kategoria,
                   (SELECT json_group_array(r.koodi) FROM tuote_ruokavalio tr
                    JOIN ruokavaliot r ON r.id = tr.ruokavalio_id WHERE tr.tuote_id = t.id) AS ruokavaliot
            FROM tuotteet t JOIN kategoriat k ON k.id = t.kategoria_id
            WHERE t.id = ?
            """,
            (tuote_id,),
        ).fetchone()
        if rivi is None:
            return None
        return {"nimi": rivi[0], "kategoria": rivi[1], "ruokavaliot": set(json.loads(rivi[2]))}


# --- yleiset tarkistukset (ajetaan jokaiselle tapaukselle) -----------------


def ei_keksittyja_tuotteita(_arvo, jalki, _kanta):
    """Jokainen laske_hinta-kutsun tuote_id on tullut aiemmin hae_tuotteet-tuloksesta."""
    nahdyt: set[int] = set()
    for k in jalki["kutsut"]:
        if k["nimi"] == "hae_tuotteet" and not k["virhe"]:
            nahdyt |= {t["id"] for t in _tulos(k) or []}
        elif k["nimi"] == "laske_hinta":
            idt = {r.get("tuote_id") for r in (k["argumentit"] or {}).get("ostoslista", [])}
            keksityt = idt - nahdyt
            if keksityt:
                return f"laske_hinta sai id:t {sorted(keksityt, key=str)}, joita mikään haku ei palauttanut"
    return None


def hinnat_tarkistettavissa(_arvo, jalki, _kanta):
    """Jokainen vastauksen euromäärä löytyy työkalujen tuloksista tai kysymyksestä.

    Sallitaan myös kysymyksen summa miinus laske_hinta-summa (budjetista jäävä raha).
    """
    sallitut = set(euromaarat_snt(jalki["kysymys"]))
    summat = set()
    for k in jalki["kutsut"]:
        tulos = _tulos(k) if not k["virhe"] else None
        # hae_tuotteet palauttaa listan, laske_hinta dictin, jossa on rivit.
        if isinstance(tulos, list):
            rivit = tulos
        elif isinstance(tulos, dict):
            rivit = tulos.get("rivit", [])
        else:
            rivit = []
        for r in rivit:
            if isinstance(r, dict):
                sallitut |= {r[a] for a in ("hinta_snt", "rivihinta_snt") if isinstance(r.get(a), int)}
        if isinstance(tulos, dict):
            if isinstance(tulos.get("yhteensa_snt"), int):
                summat.add(tulos["yhteensa_snt"])
            # laske_hinta laskee budjetin ylityksen tai jäännöksen itse.
            sallitut |= {tulos[a] for a in ("ylitys_snt", "jaljella_snt") if isinstance(tulos.get(a), int)}
    sallitut |= summat
    sallitut |= {b - s for b in euromaarat_snt(jalki["kysymys"]) for s in summat}
    tuntemattomat = euromaarat_snt(jalki["vastaus"]) - sallitut
    if tuntemattomat:
        return "vastauksessa hintoja, joita ei saatu työkaluilta: " + ", ".join(
            f"{s // 100},{s % 100:02d} €" for s in sorted(tuntemattomat)
        )
    return None


def ei_ruokavaliota_hakusanassa(_arvo, jalki, _kanta):
    for a in _hakuargumentit(jalki):
        sana = str(a.get("hakusana", "")).casefold()
        if any(r in sana for r in RUOKAVALIOSANAT):
            return f"ruokavalio hakusanassa: '{a.get('hakusana')}'"
    return None


YLEISET = {
    "ei_keksittyja_tuotteita": ei_keksittyja_tuotteita,
    "hinnat_tarkistettavissa": hinnat_tarkistettavissa,
    "ei_ruokavaliota_hakusanassa": ei_ruokavaliota_hakusanassa,
}


# --- tapauskohtaiset tarkistukset -----------------------------------------


def laske_hinta_kutsuttu(arvo, jalki, _kanta):
    if arvo and ostoslista(jalki) is None:
        return "ostoslistaa ei laskettu laske_hinta-työkalulla"
    return None


def tyokalu_kutsuttu(arvo, jalki, _kanta):
    puuttuvat = [n for n in arvo if not any(k["nimi"] == n for k in jalki["kutsut"])]
    return f"ei kutsuttu: {', '.join(puuttuvat)}" if puuttuvat else None


def ei_tyokalua(arvo, jalki, _kanta):
    kutsutut = [n for n in arvo if any(k["nimi"] == n for k in jalki["kutsut"])]
    return f"kutsuttiin, vaikka ei pitänyt: {', '.join(kutsutut)}" if kutsutut else None


def max_hakuja(arvo, jalki, _kanta):
    n = len(_hakuargumentit(jalki))
    return f"{n} hae_tuotteet-kutsua, raja {arvo}" if n > arvo else None


def max_tyokalukutsuja(arvo, jalki, _kanta):
    n = len(jalki["kutsut"])
    return f"{n} työkalukutsua, raja {arvo}" if n > arvo else None


def max_kierroksia(arvo, jalki, _kanta):
    n = jalki["kierrokset"]
    return f"{n} kierrosta, raja {arvo}" if n > arvo else None


def ei_tyokaluvirheita(arvo, jalki, _kanta):
    virheet = [k["tulos"] for k in jalki["kutsut"] if k["virhe"]]
    return f"työkaluvirheitä: {virheet[0][:120]}" if arvo and virheet else None


def budjetti_max_snt(arvo, jalki, _kanta):
    """Budjetti ei saa ylittyä, paitsi jos agentti kertoo ylityksen työkalun luvulla.

    Mahdottomassa budjetissa oikea käytös on halvin lista + ylitys selvästi kerrottuna.
    Poikkeus vaatii, että laske_hinta sai saman budjetin ja ilmoitti budjetti_ylittyy,
    ja että vastaus mainitsee ylityksen ja sen euromäärän (ylitys_snt).
    """
    lista = ostoslista(jalki)
    if not lista or lista["yhteensa_snt"] <= arvo:
        return None
    perus = f"yhteensä {lista['yhteensa_snt']} snt, budjetti {arvo} snt"
    if lista.get("budjetti_snt") != arvo or not lista.get("budjetti_ylittyy"):
        return f"{perus}; budjettia {arvo} ei annettu laske_hinta-kutsuun"
    ylitys = lista["ylitys_snt"]
    if "ylit" not in _vastaus(jalki):
        return f"{perus}; vastaus ei kerro budjetin ylittyvän"
    if ylitys not in euromaarat_snt(jalki["vastaus"]):
        return f"{perus}; vastauksesta puuttuu ylitys {ylitys // 100},{ylitys % 100:02d} €"
    return None


def yhteensa_snt(arvo, jalki, _kanta):
    lista = ostoslista(jalki)
    if lista is None:
        return "ostoslistaa ei laskettu"
    return f"yhteensä {lista['yhteensa_snt']} snt, odotettiin {arvo}" if lista["yhteensa_snt"] != arvo else None


def summa_vastauksessa(arvo, jalki, _kanta):
    lista = ostoslista(jalki)
    if not arvo:
        return None
    if lista is None:
        return "ostoslistaa ei laskettu"
    if lista["yhteensa_snt"] not in euromaarat_snt(jalki["vastaus"]):
        s = lista["yhteensa_snt"]
        return f"laske_hinta-summa {s // 100},{s % 100:02d} € puuttuu vastauksesta"
    return None


def ruokavaliot(arvo, jalki, kanta):
    lista = ostoslista(jalki)
    for r in (lista or {}).get("rivit", []):
        tiedot = kanta.hae(r["tuote_id"])
        puuttuu = set(arvo) - (tiedot["ruokavaliot"] if tiedot else set())
        if puuttuu:
            return f"{r['nimi']}: ei merkintää {', '.join(sorted(puuttuu))}"
    return None


def kategoriat_kaikki(arvo, jalki, kanta):
    lista = ostoslista(jalki)
    if lista is None:
        return "ostoslistaa ei laskettu"
    kategoriat = {(kanta.hae(r["tuote_id"]) or {}).get("kategoria") for r in lista["rivit"]}
    puuttuvat = [" tai ".join(ryhma) for ryhma in arvo if not kategoriat & set(ryhma)]
    return f"listalta puuttuu kategoria: {'; '.join(puuttuvat)}" if puuttuvat else None


def tuotteet_mukana(arvo, jalki, _kanta):
    lista = ostoslista(jalki)
    if lista is None:
        return "ostoslistaa ei laskettu"
    nimet = [r["nimi"].casefold() for r in lista["rivit"]]
    puuttuvat = [
        " tai ".join(ryhma) for ryhma in arvo if not any(s.casefold() in n for s in ryhma for n in nimet)
    ]
    return f"listalta puuttuu: {'; '.join(puuttuvat)}" if puuttuvat else None


def tuotteet_ei_mukana(arvo, jalki, _kanta):
    lista = ostoslista(jalki)
    nimet = [r["nimi"] for r in (lista or {}).get("rivit", [])]
    kielletyt = [n for n in nimet if any(s.casefold() in n.casefold() for s in arvo)]
    return f"listalla ei-toivottu tuote: {', '.join(kielletyt)}" if kielletyt else None


def haku_kategorialla(arvo, jalki, _kanta):
    if not any(a.get("kategoria") in arvo for a in _hakuargumentit(jalki)):
        return f"ei hakua kategorialla {' tai '.join(arvo)}"
    return None


def haku_ruokavaliolla(arvo, jalki, _kanta):
    if not any(set(arvo) <= set(a.get("ruokavaliot") or []) for a in _hakuargumentit(jalki)):
        return f"ei hakua ruokavaliolla {', '.join(arvo)}"
    return None


def vastaus_sisaltaa_kaikki(arvo, jalki, _kanta):
    puuttuvat = [s for s in arvo if s.casefold() not in _vastaus(jalki)]
    return f"vastauksesta puuttuu: {', '.join(puuttuvat)}" if puuttuvat else None


def vastaus_sisaltaa_jokin(arvo, jalki, _kanta):
    if not any(s.casefold() in _vastaus(jalki) for s in arvo):
        return f"vastauksessa ei mitään näistä: {', '.join(arvo)}"
    return None


def vastaus_ei_sisalla(arvo, jalki, _kanta):
    loytyi = [s for s in arvo if s.casefold() in _vastaus(jalki)]
    return f"vastauksessa ei-toivottua: {', '.join(loytyi)}" if loytyi else None


def vastaus_kysyy(arvo, jalki, _kanta):
    return "vastaus ei sisällä tarkentavaa kysymystä" if arvo and "?" not in (jalki["vastaus"] or "") else None


TAPAUSKOHTAISET: dict[str, Callable] = {
    f.__name__: f
    for f in (
        laske_hinta_kutsuttu,
        tyokalu_kutsuttu,
        ei_tyokalua,
        max_hakuja,
        max_tyokalukutsuja,
        max_kierroksia,
        ei_tyokaluvirheita,
        budjetti_max_snt,
        yhteensa_snt,
        summa_vastauksessa,
        ruokavaliot,
        kategoriat_kaikki,
        tuotteet_mukana,
        tuotteet_ei_mukana,
        haku_kategorialla,
        haku_ruokavaliolla,
        vastaus_sisaltaa_kaikki,
        vastaus_sisaltaa_jokin,
        vastaus_ei_sisalla,
        vastaus_kysyy,
    )
}


def tarkista(tapaus: dict, jalki: dict, kanta: Tuotetiedot) -> dict[str, str | None]:
    """Ajaa yleiset ja tapauksen omat tarkistukset. Palauttaa {nimi: syy tai None}."""
    tulokset = {nimi: f(None, jalki, kanta) for nimi, f in YLEISET.items()}
    for nimi, arvo in tapaus["ehdot"].items():
        tulokset[nimi] = TAPAUSKOHTAISET[nimi](arvo, jalki, kanta)
    return tulokset


def validoi_tapaukset(tapaukset: list[dict]) -> None:
    """Kaatuu ennen ajoa, jos tiedostossa on kirjoitusvirhe. Halvempi kuin turha API-ajo."""
    idt = [t["id"] for t in tapaukset]
    if len(idt) != len(set(idt)):
        raise ValueError("tapausten id:t eivät ole yksilöllisiä")
    for t in tapaukset:
        if not t.get("kysymys") or not t.get("ehdot"):
            raise ValueError(f"{t['id']}: kysymys tai ehdot puuttuu")
        tuntemattomat = set(t["ehdot"]) - set(TAPAUSKOHTAISET)
        if tuntemattomat:
            raise ValueError(f"{t['id']}: tuntematon ehto {sorted(tuntemattomat)}")
