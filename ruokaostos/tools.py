"""Agentin työkalufunktiot tuotetietokantaan.

Kaikki SQL on staattista ja parametrisoitua. Vaihtuvanmittaiset listat
välitetään yhtenä JSON-parametrina ja puretaan SQLiten json_each():llä, joten
kyselyä ei koskaan rakenneta merkkijonoista.

Virheelliset syötteet nostavat TyokaluVirhe-poikkeuksen, jonka viesti on
kirjoitettu niin, että agentti voi korjata kutsunsa sen perusteella.
"""

import json
import sqlite3
from decimal import ROUND_HALF_UP, Decimal
from numbers import Real

from ruokaostos import db


class TyokaluVirhe(ValueError):
    """Agentin korjattavissa oleva virhe työkalukutsun syötteessä."""


_HAE_TUOTTEET_SQL = """
SELECT t.id, t.nimi, k.nimi AS kategoria, t.hinta_snt, t.yksikko,
       (SELECT json_group_array(r.koodi) FROM tuote_ruokavalio tr
        JOIN ruokavaliot r ON r.id = tr.ruokavalio_id
        WHERE tr.tuote_id = t.id) AS ruokavaliot
FROM tuotteet t
JOIN kategoriat k ON k.id = t.kategoria_id
WHERE instr(casefold(t.nimi), :hakusana) > 0
  AND (:kategoria IS NULL OR casefold(k.nimi) = :kategoria)
  AND (:max_hinta IS NULL OR t.hinta_snt <= :max_hinta)
  AND (:ruokavalio_lkm = 0 OR t.id IN (
        SELECT tr.tuote_id FROM tuote_ruokavalio tr
        JOIN ruokavaliot r ON r.id = tr.ruokavalio_id
        WHERE r.koodi IN (SELECT value FROM json_each(:ruokavaliot))
        GROUP BY tr.tuote_id
        HAVING COUNT(*) = :ruokavalio_lkm))
ORDER BY t.hinta_snt, t.nimi
LIMIT :raja
"""


def _yhteys(conn: sqlite3.Connection | None) -> sqlite3.Connection:
    return conn if conn is not None else db.connect()


def _on_kokonaisluku(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def hae_tuotteet(
    hakusana: str,
    kategoria: str | None = None,
    ruokavaliot: list[str] | None = None,
    max_hinta_snt: int | None = None,
    *,
    raja: int = 50,
    conn: sqlite3.Connection | None = None,
) -> list[dict]:
    """Hakee tuotteet, joiden nimessä esiintyy hakusana (kirjainkoosta riippumatta).

    Tyhjä hakusana palauttaa kaikki suodattimet täyttävät tuotteet. Tuotteen on
    täytettävä KAIKKI annetut ruokavaliot. Tulokset ovat halvimmasta kalleimpaan.
    """
    if not isinstance(hakusana, str):
        raise TyokaluVirhe("hakusana pitää olla merkkijono")
    if max_hinta_snt is not None and (not _on_kokonaisluku(max_hinta_snt) or max_hinta_snt < 0):
        raise TyokaluVirhe("max_hinta_snt pitää olla ei-negatiivinen kokonaisluku (sentteinä)")
    if not _on_kokonaisluku(raja) or raja < 1:
        raise TyokaluVirhe("raja pitää olla positiivinen kokonaisluku")

    conn = _yhteys(conn)

    if kategoria is not None:
        kategoria = kategoria.strip().casefold()
        tunnetut = {k["nimi"].casefold() for k in listaa_kategoriat(conn=conn)}
        if kategoria not in tunnetut:
            raise TyokaluVirhe(
                f"Tuntematon kategoria {kategoria!r}. Käytä listaa_kategoriat()-työkalua."
            )

    koodit = sorted({r.strip().casefold() for r in ruokavaliot or []})
    tunnetut_koodit = {r["koodi"] for r in listaa_ruokavaliot(conn=conn)}
    if tuntemattomat := [k for k in koodit if k not in tunnetut_koodit]:
        raise TyokaluVirhe(
            f"Tuntemattomat ruokavaliot {tuntemattomat}. Sallitut: {sorted(tunnetut_koodit)}"
        )

    rivit = conn.execute(
        _HAE_TUOTTEET_SQL,
        {
            "hakusana": hakusana.strip().casefold(),
            "kategoria": kategoria,
            "max_hinta": max_hinta_snt,
            "ruokavaliot": json.dumps(koodit),
            "ruokavalio_lkm": len(koodit),
            "raja": raja,
        },
    ).fetchall()
    return [{**dict(r), "ruokavaliot": sorted(json.loads(r["ruokavaliot"]))} for r in rivit]


def listaa_kategoriat(*, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Palauttaa kaikki kategoriat tuotemäärineen aakkosjärjestyksessä."""
    rivit = _yhteys(conn).execute(
        """SELECT k.id, k.nimi, COUNT(t.id) AS tuotteita
           FROM kategoriat k LEFT JOIN tuotteet t ON t.kategoria_id = k.id
           GROUP BY k.id ORDER BY k.nimi"""
    )
    return [dict(r) for r in rivit]


def listaa_ruokavaliot(*, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Palauttaa ruokavaliot (koodi hakuja varten, nimi näyttämistä varten) tuotemäärineen."""
    rivit = _yhteys(conn).execute(
        """SELECT r.koodi, r.nimi, COUNT(tr.tuote_id) AS tuotteita
           FROM ruokavaliot r LEFT JOIN tuote_ruokavalio tr ON tr.ruokavalio_id = r.id
           GROUP BY r.id ORDER BY r.id"""
    )
    return [dict(r) for r in rivit]


def laske_hinta(ostoslista: list[dict], *, conn: sqlite3.Connection | None = None) -> dict:
    """Laskee ostoslistan hinnan.

    ostoslista: [{"tuote_id": int, "maara": luku}, ...]. Määrä on tuotteen
    yksikössä: kpl-tuotteille kokonaisluku, kg/l-tuotteille desimaali sallittu.
    Rivihinnat pyöristetään lähimpään senttiin (puolikkaat ylöspäin).
    """
    if not isinstance(ostoslista, list):
        raise TyokaluVirhe("ostoslista pitää olla lista muotoa [{tuote_id, maara}]")
    for i, rivi in enumerate(ostoslista):
        if not isinstance(rivi, dict) or not _on_kokonaisluku(rivi.get("tuote_id")):
            raise TyokaluVirhe(f"Rivi {i}: tuote_id puuttuu tai ei ole kokonaisluku")
        maara = rivi.get("maara")
        if not isinstance(maara, Real) or isinstance(maara, bool) or maara <= 0:
            raise TyokaluVirhe(f"Rivi {i}: maara pitää olla positiivinen luku")

    tuotteet = {
        r["id"]: r
        for r in _yhteys(conn).execute(
            """SELECT id, nimi, hinta_snt, yksikko FROM tuotteet
               WHERE id IN (SELECT value FROM json_each(?))""",
            (json.dumps([r["tuote_id"] for r in ostoslista]),),
        )
    }
    if puuttuvat := sorted({r["tuote_id"] for r in ostoslista} - tuotteet.keys()):
        raise TyokaluVirhe(f"Tuntemattomat tuote_id:t {puuttuvat}. Hae id:t hae_tuotteet()-työkalulla.")

    rivit = []
    for i, rivi in enumerate(ostoslista):
        t = tuotteet[rivi["tuote_id"]]
        maara = rivi["maara"]
        if t["yksikko"] == "kpl" and maara != int(maara):
            raise TyokaluVirhe(f"Rivi {i}: {t['nimi']} myydään kappaleittain, määrän pitää olla kokonaisluku")
        # Decimal(str(...)), jotta esim. 0.35 kg ei muutu muotoon 0.34999...
        rivihinta = (Decimal(t["hinta_snt"]) * Decimal(str(maara))).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
        rivit.append(
            {
                "tuote_id": t["id"],
                "nimi": t["nimi"],
                "yksikko": t["yksikko"],
                "hinta_snt": t["hinta_snt"],
                "maara": maara,
                "rivihinta_snt": int(rivihinta),
            }
        )
    return {"rivit": rivit, "yhteensa_snt": sum(r["rivihinta_snt"] for r in rivit)}
