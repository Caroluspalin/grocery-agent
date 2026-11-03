"""Eval-tarkistusten ja -ajajan testit valmiilla jäljillä: API:a ei kutsuta.

Itse evalit (evals/aja.py) eivät ole osa pytestiä, koska ne maksavat. Tässä
testataan, että mittatikku itse mittaa oikein.
"""

import json
from types import SimpleNamespace

import pytest

from evals import aja, tarkistukset as t
from ruokaostos import tools


@pytest.fixture
def kanta(conn):
    return t.Tuotetiedot(conn)


def id_(conn, nimi):
    return conn.execute("SELECT id FROM tuotteet WHERE nimi = ?", (nimi,)).fetchone()[0]


def kutsu(nimi, argumentit, tulos, virhe=False):
    return {
        "nimi": nimi,
        "argumentit": argumentit,
        "tulos": tulos if isinstance(tulos, str) else json.dumps(tulos, ensure_ascii=False),
        "virhe": virhe,
    }


def jalki(kysymys="x", vastaus="", kutsut=(), kierrokset=1):
    return {"kysymys": kysymys, "vastaus": vastaus, "kutsut": list(kutsut), "kierrokset": kierrokset}


def haku_ja_lasku(conn, hakusana, kategoria, nimet_ja_maarat, ruokavaliot=None, budjetti_snt=None):
    """Oikeat työkalutulokset kannasta: yksi haku ja laske_hinta."""
    argumentit = {"hakusana": hakusana, "kategoria": kategoria, "ruokavaliot": ruokavaliot}
    argumentit = {k: v for k, v in argumentit.items() if v is not None}
    hakutulos = tools.hae_tuotteet(**argumentit, raja=200, conn=conn)
    lista = [{"tuote_id": id_(conn, n), "maara": m} for n, m in nimet_ja_maarat]
    lasku = {"ostoslista": lista} | ({"budjetti_snt": budjetti_snt} if budjetti_snt is not None else {})
    return [
        kutsu("hae_tuotteet", argumentit, hakutulos),
        kutsu("laske_hinta", lasku, tools.laske_hinta(lista, budjetti_snt, conn=conn)),
    ]


# --- tapaukset.json -------------------------------------------------------


def test_tapaukset_ovat_kelvollisia():
    tapaukset = json.loads(aja.TAPAUKSET.read_text(encoding="utf-8"))
    t.validoi_tapaukset(tapaukset)
    assert len(tapaukset) == 25


def test_jokainen_ehto_toimii_tyhjalla_jaljella(kanta):
    """Kirjoitusvirhe ehdon arvossa (esim. lista vs. luku) kaatuisi vasta maksullisessa ajossa."""
    for tapaus in json.loads(aja.TAPAUKSET.read_text(encoding="utf-8")):
        tulokset = t.tarkista(tapaus, jalki(tapaus["kysymys"]), kanta)
        assert set(tulokset) >= set(tapaus["ehdot"])


def test_validointi_hylkaa_tuntemattoman_ehdon():
    with pytest.raises(ValueError, match="tuntematon ehto"):
        t.validoi_tapaukset([{"id": "a", "kysymys": "x", "ehdot": {"budjetti_max": 100}}])


def test_validointi_hylkaa_tuplaidn():
    tapaus = {"id": "a", "kysymys": "x", "ehdot": {"vastaus_kysyy": True}}
    with pytest.raises(ValueError, match="yksilöllisiä"):
        t.validoi_tapaukset([tapaus, tapaus])


# --- euromäärät -----------------------------------------------------------


@pytest.mark.parametrize(
    "teksti, odotettu",
    [
        ("Yhteensä 3,49 €", {349}),
        ("budjetti 10 €", {1000}),
        ("10 euroa ja 2,5 €", {1000, 250}),
        ("hinta 1.99€/kg", {199}),
        ("Kevytmaito 1 l, 2 kpl", set()),
    ],
)
def test_euromaarat_snt(teksti, odotettu):
    assert t.euromaarat_snt(teksti) == odotettu


# --- yleiset tarkistukset -------------------------------------------------


def test_keksitty_tuote_id_havaitaan(conn, kanta):
    j = jalki(kutsut=[kutsu("laske_hinta", {"ostoslista": [{"tuote_id": 1, "maara": 1}]}, {"rivit": [], "yhteensa_snt": 0})])
    assert "id:t [1]" in t.ei_keksittyja_tuotteita(None, j, kanta)


def test_haetusta_tuotteesta_laskettu_on_ok(conn, kanta):
    j = jalki(kutsut=haku_ja_lasku(conn, "banaani", None, [("Banaani", 1)]))
    assert t.ei_keksittyja_tuotteita(None, j, kanta) is None


def test_keksitty_hinta_havaitaan(conn, kanta):
    j = jalki(vastaus="Banaani maksaa 1,49 €/kg.", kutsut=haku_ja_lasku(conn, "banaani", None, [("Banaani", 1)]))
    assert "1,49 €" in t.hinnat_tarkistettavissa(None, j, kanta)


def test_tyokalujen_hinnat_ja_budjetin_jaannos_sallitaan(conn, kanta):
    j = jalki(
        kysymys="Banaania, budjetti 10 €",
        vastaus="Banaani 1,99 €/kg, yhteensä 1,99 €. Budjetista jää 8,01 €.",
        kutsut=haku_ja_lasku(conn, "banaani", None, [("Banaani", 1)]),
    )
    assert t.hinnat_tarkistettavissa(None, j, kanta) is None


def test_ruokavalio_hakusanassa_havaitaan(kanta):
    j = jalki(kutsut=[kutsu("hae_tuotteet", {"hakusana": "juusto gluteeniton"}, [])])
    assert "juusto gluteeniton" in t.ei_ruokavaliota_hakusanassa(None, j, kanta)


# --- tapauskohtaiset tarkistukset -----------------------------------------


def test_ylikorjattu_aamupala_epaonnistuu(conn, kanta):
    """Oikea virhe: pelkkä Aamupala-kategoria, ei juustoa eikä maitoa."""
    j = jalki(kutsut=haku_ja_lasku(conn, "", "Aamupala", [("Gluteenittomat kaurahiutaleet 500 g", 1)], ["gluteeniton"]))
    ehto = [["Aamupala"], ["Juustot", "Maitotuotteet ja kasvijuomat"]]
    assert "Juustot tai Maitotuotteet" in t.kategoriat_kaikki(ehto, j, kanta)


def test_aamupala_ja_juusto_onnistuu(conn, kanta):
    kutsut = haku_ja_lasku(conn, "", "Aamupala", [("Gluteenittomat kaurahiutaleet 500 g", 1)], ["gluteeniton"])
    kutsut += haku_ja_lasku(
        conn, "", "Juustot", [("Gluteenittomat kaurahiutaleet 500 g", 1), ("Edam viipale 300 g", 1)], ["gluteeniton"]
    )
    ehto = [["Aamupala"], ["Juustot", "Maitotuotteet ja kasvijuomat"]]
    j = jalki(kutsut=kutsut)
    assert t.kategoriat_kaikki(ehto, j, kanta) is None
    assert t.ruokavaliot(["gluteeniton"], j, kanta) is None


def test_ruokavalio_tarkistetaan_kannasta(conn, kanta):
    j = jalki(kutsut=haku_ja_lasku(conn, "kaurahiutaleet", None, [("Kaurahiutaleet 1 kg", 1)]))
    assert "Kaurahiutaleet 1 kg" in t.ruokavaliot(["gluteeniton"], j, kanta)


def test_budjetti(conn, kanta):
    j = jalki(kutsut=haku_ja_lasku(conn, "banaani", None, [("Banaani", 2)]))
    assert t.budjetti_max_snt(398, j, kanta) is None
    assert "398 snt" in t.budjetti_max_snt(397, j, kanta)


def test_budjetti_ilman_listaa_ei_kaada(kanta):
    """Mahdoton budjetti: agentti saa kieltäytyä tekemästä listaa."""
    assert t.budjetti_max_snt(500, jalki(), kanta) is None
    assert t.laske_hinta_kutsuttu(True, jalki(), kanta) is not None


def test_viimeinen_laske_hinta_ratkaisee(conn, kanta):
    kutsut = haku_ja_lasku(conn, "banaani", None, [("Banaani", 10)]) + haku_ja_lasku(conn, "banaani", None, [("Banaani", 1)])
    assert t.yhteensa_snt(199, jalki(kutsut=kutsut), kanta) is None


def test_summa_vastauksessa(conn, kanta):
    kutsut = haku_ja_lasku(conn, "banaani", None, [("Banaani", 2)])
    assert t.summa_vastauksessa(True, jalki(vastaus="Yhteensä 3,98 €", kutsut=kutsut), kanta) is None
    assert t.summa_vastauksessa(True, jalki(vastaus="Yhteensä noin 4 €", kutsut=kutsut), kanta) is not None


def test_haku_kategorialla_ja_ruokavaliolla(kanta):
    j = jalki(kutsut=[kutsu("hae_tuotteet", {"hakusana": "", "kategoria": "Juustot", "ruokavaliot": ["gluteeniton", "laktoositon"]}, [])])
    assert t.haku_kategorialla(["Juustot"], j, kanta) is None
    assert t.haku_ruokavaliolla(["gluteeniton"], j, kanta) is None
    assert t.haku_kategorialla(["Aamupala"], j, kanta) is not None


def test_vastaustarkistukset(kanta):
    j = jalki(vastaus="Valitettavasti hummeria ei ole valikoimassa. Haluatko kalaa?")
    assert t.vastaus_sisaltaa_jokin(["ei ole"], j, kanta) is None
    assert t.vastaus_sisaltaa_kaikki(["hummeri", "rapu"], j, kanta) == "vastauksesta puuttuu: rapu"
    assert t.vastaus_ei_sisalla(["HUMMERIA"], j, kanta) is not None
    assert t.vastaus_kysyy(True, j, kanta) is None


# --- ajaja valeasiakkaalla ------------------------------------------------


def _vastaus(teksti=None, kutsut=None, tokenit=(100, 20)):
    viesti = SimpleNamespace(content=teksti, tool_calls=kutsut, refusal=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="tool_calls" if kutsut else "stop", message=viesti)],
        usage=SimpleNamespace(prompt_tokens=tokenit[0], completion_tokens=tokenit[1]),
    )


def _kutsu(nimi, argumentit, id_="c1"):
    return SimpleNamespace(id=id_, type="function", function=SimpleNamespace(name=nimi, arguments=argumentit))


class ValeClient:
    def __init__(self, *vastaukset):
        self.vastaukset = list(vastaukset)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **_: self.vastaukset.pop(0)))


def test_aja_tapaus_kerää_jaljen_ja_tokenit(conn):
    client = ValeClient(
        _vastaus(kutsut=[_kutsu("hae_tuotteet", '{"hakusana": "banaani"}'), _kutsu("listaa_kategoriat", "{rikki", "c2")]),
        _vastaus("Banaani 1,99 €/kg."),
    )
    j = aja.aja_tapaus({"kysymys": "banaani?"}, client, conn, "testimalli")
    assert j["vastaus"] == "Banaani 1,99 €/kg."
    assert j["virhe"] is None
    assert j["kierrokset"] == 2
    assert j["tokenit"] == {"syote": 200, "tuloste": 40}
    hae, rikki = j["kutsut"]
    assert hae["argumentit"] == {"hakusana": "banaani"} and not hae["virhe"]
    assert rikki["argumentit"] is None and rikki["virhe"]


def test_aja_tapaus_kirjaa_agentin_virheen(conn):
    client = ValeClient(_vastaus())
    client.chat.completions.create = lambda **_: (_ for _ in ()).throw(RuntimeError("katkesi"))
    j = aja.aja_tapaus({"kysymys": "x"}, client, conn, "m")
    assert j["virhe"] == "RuntimeError: katkesi"
    assert j["vastaus"] is None


def test_yhteenveto():
    tulokset = [
        {"onnistui": True, "jalki": {"kierrokset": 2, "kutsut": [1, 2], "tokenit": {"syote": 10, "tuloste": 1}}},
        {"onnistui": False, "jalki": {"kierrokset": 4, "kutsut": [1], "tokenit": {"syote": 20, "tuloste": 2}}},
    ]
    y = aja.yhteenveto(tulokset)
    assert y["onnistumisprosentti"] == 50.0
    assert y["kierroksia_keskimaarin"] == 3.0
    assert y["tyokalukutsuja_yhteensa"] == 3
    assert (y["syote_tokenit"], y["tuloste_tokenit"]) == (30, 3)


# --- budjetin ylitys ------------------------------------------------------
# Lohifilee 19,99 €, budjetti 3 € → ylitys 16,99 €.


def _lohi(conn, vastaus, budjetti_snt=300):
    return jalki(
        kysymys="Kilo lohta, budjetti 3 €",
        vastaus=vastaus,
        kutsut=haku_ja_lasku(conn, "lohifilee", None, [("Lohifilee", 1)], budjetti_snt=budjetti_snt),
    )


def test_ylitys_kerrottuna_tyokalun_luvulla_menee_lapi(conn, kanta):
    j = _lohi(conn, "Halvin lista: Lohifilee 19,99 €. Budjetti ylittyy 16,99 €.")
    assert t.budjetti_max_snt(300, j, kanta) is None
    assert t.hinnat_tarkistettavissa(None, j, kanta) is None


def test_ylitysta_ei_mainita(conn, kanta):
    j = _lohi(conn, "Lohifilee 19,99 €, yhteensä 19,99 €.")
    assert "ei kerro budjetin ylittyvän" in t.budjetti_max_snt(300, j, kanta)


def test_ylityksen_maara_vaarin(conn, kanta):
    j = _lohi(conn, "Budjetti ylittyy noin 17 €.")
    assert "puuttuu ylitys 16,99 €" in t.budjetti_max_snt(300, j, kanta)


def test_ylitys_ilman_budjettia_laske_hinnassa(conn, kanta):
    j = _lohi(conn, "Budjetti ylittyy 16,99 €.", budjetti_snt=None)
    assert "ei annettu laske_hinta-kutsuun" in t.budjetti_max_snt(300, j, kanta)


def test_vaara_budjetti_laske_hinnassa(conn, kanta):
    """Agentti antoi 30 € budjetiksi 3 €:n sijaan: poikkeus ei päde."""
    j = _lohi(conn, "Budjetti riittää.", budjetti_snt=3000)
    assert "ei annettu laske_hinta-kutsuun" in t.budjetti_max_snt(300, j, kanta)


def test_jaljella_oleva_raha_tyokalusta_sallitaan(conn, kanta):
    j = jalki(
        kysymys="Banaania",
        vastaus="Yhteensä 1,99 €, budjetista jää 8,01 €.",
        kutsut=haku_ja_lasku(conn, "banaani", None, [("Banaani", 1)], budjetti_snt=1000),
    )
    assert t.hinnat_tarkistettavissa(None, j, kanta) is None


# --- useat ajot -----------------------------------------------------------


def _ajo(**onnistumiset):
    jalki_ = {"kierrokset": 1, "kutsut": [], "tokenit": {"syote": 0, "tuloste": 0}}
    return [{"id": i, "onnistui": o, "jalki": jalki_} for i, o in onnistumiset.items()]


def test_ajojen_vertailu():
    ajot = [
        _ajo(a=True, b=False, c=True, d=False),
        _ajo(a=True, b=False, c=False, d=False),
        _ajo(a=True, b=False, c=True, d=True),
    ]
    v = aja.ajojen_vertailu(ajot)
    assert v["prosentit"] == [50.0, 25.0, 75.0]
    assert v["keskiarvo"] == 50.0
    assert (v["pienin"], v["suurin"]) == (25.0, 75.0)
    assert v["aina_epaonnistuneet"] == ["b"]
    assert v["vaihtelevat"] == {"c": "2/3", "d": "1/3"}


def test_ajojen_vertailu_tulostuu(capsys):
    aja.tulosta_vertailu(aja.ajojen_vertailu([_ajo(a=True), _ajo(a=True)]))
    tuloste = capsys.readouterr().out
    assert "Keskiarvo 100.0 %" in tuloste
    assert tuloste.count("(ei yhtään)") == 2


def test_keskeytetty_ajo_sailyttaa_tulokset(conn, kanta):
    vastaukset = iter([_vastaus("ok")])

    def create(**_):
        try:
            return next(vastaukset)
        except StopIteration:
            raise KeyboardInterrupt

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    tapaukset = [{"id": "a", "kysymys": "x", "ehdot": {}}, {"id": "b", "kysymys": "y", "ehdot": {}}]
    with pytest.raises(KeyboardInterrupt) as e:
        aja.aja_setti(tapaukset, client, conn, kanta, "m")
    assert [t["id"] for t in e.value.tulokset] == ["a"]


def test_tallennus_ei_ylikirjoita(tmp_path, monkeypatch):
    from datetime import datetime

    monkeypatch.setattr(aja, "TULOKSET", tmp_path)
    hetki = datetime(2026, 11, 3, 12, 0, 0)
    eka = aja.tallenna(_ajo(a=True), hetki, "m")
    toka = aja.tallenna(_ajo(a=False), hetki, "m")
    assert eka != toka and eka.exists() and toka.exists()
