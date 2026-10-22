import pytest

from ruokaostos.tools import (
    TyokaluVirhe,
    hae_tuotteet,
    laske_hinta,
    listaa_kategoriat,
    listaa_ruokavaliot,
)


def _id(conn, nimi):
    return conn.execute("SELECT id FROM tuotteet WHERE nimi = ?", (nimi,)).fetchone()["id"]


# --- hae_tuotteet ---------------------------------------------------------


def test_haku_palauttaa_dictit_oikeilla_kentilla(conn):
    [banaani] = hae_tuotteet("banaani", conn=conn)
    assert banaani == {
        "id": banaani["id"],
        "nimi": "Banaani",
        "kategoria": "Hedelmät ja vihannekset",
        "hinta_snt": 199,
        "yksikko": "kg",
        "ruokavaliot": ["gluteeniton", "laktoositon", "vegaaninen"],
    }


def test_haku_on_kirjainkoosta_riippumaton_myos_aakkosille(conn):
    assert [t["nimi"] for t in hae_tuotteet("PÄÄRYNÄ", conn=conn)] == ["Päärynä"]


def test_haku_osuu_nimen_keskelle(conn):
    nimet = {t["nimi"] for t in hae_tuotteet("jogurtti", conn=conn)}
    assert "Mansikkajogurtti 150 g" in nimet
    assert "Turkkilainen jogurtti 10 % 1 kg" in nimet


def test_tyhja_hakusana_palauttaa_kaiken_rajaan_asti(conn):
    assert len(hae_tuotteet("", raja=1000, conn=conn)) == 237
    assert len(hae_tuotteet("", conn=conn)) == 50


def test_tulokset_ovat_hintajarjestyksessa(conn):
    hinnat = [t["hinta_snt"] for t in hae_tuotteet("", raja=1000, conn=conn)]
    assert hinnat == sorted(hinnat)


def test_kategoriasuodatus(conn):
    tulos = hae_tuotteet("", kategoria="juustot", raja=1000, conn=conn)
    assert len(tulos) == 13
    assert {t["kategoria"] for t in tulos} == {"Juustot"}


def test_tuntematon_kategoria_on_virhe(conn):
    with pytest.raises(TyokaluVirhe, match="listaa_kategoriat"):
        hae_tuotteet("", kategoria="Elektroniikka", conn=conn)


def test_maksimihinta(conn):
    tulos = hae_tuotteet("", max_hinta_snt=100, raja=1000, conn=conn)
    assert tulos
    assert all(t["hinta_snt"] <= 100 for t in tulos)


@pytest.mark.parametrize("arvo", [-1, 1.5, "100", True])
def test_virheellinen_maksimihinta(conn, arvo):
    with pytest.raises(TyokaluVirhe):
        hae_tuotteet("", max_hinta_snt=arvo, conn=conn)


def test_ruokavaliot_vaativat_kaikki_ehdot(conn):
    # Kaurajuoma on vegaaninen mutta ei gluteeniton, soijajuoma on molempia.
    pelkka_v = {t["nimi"] for t in hae_tuotteet("juoma", ruokavaliot=["vegaaninen"], conn=conn)}
    molemmat = {
        t["nimi"]
        for t in hae_tuotteet("juoma", ruokavaliot=["vegaaninen", "gluteeniton"], conn=conn)
    }
    assert {"Kaurajuoma 1 l", "Soijajuoma 1 l"} <= pelkka_v
    assert "Soijajuoma 1 l" in molemmat
    assert "Kaurajuoma 1 l" not in molemmat


def test_ruokavaliosuodatus_on_tiukka_koko_datalle(conn):
    vaaditut = {"gluteeniton", "laktoositon"}
    tulos = hae_tuotteet("", ruokavaliot=list(vaaditut), raja=1000, conn=conn)
    assert tulos
    assert all(vaaditut <= set(t["ruokavaliot"]) for t in tulos)


def test_ruokavalioiden_tuplat_ja_kirjainkoko_normalisoidaan(conn):
    a = hae_tuotteet("", ruokavaliot=["Vegaaninen", "vegaaninen "], raja=1000, conn=conn)
    b = hae_tuotteet("", ruokavaliot=["vegaaninen"], raja=1000, conn=conn)
    assert a == b


def test_tuntematon_ruokavalio_on_virhe(conn):
    with pytest.raises(TyokaluVirhe, match="pähkinätön"):
        hae_tuotteet("", ruokavaliot=["pähkinätön"], conn=conn)


@pytest.mark.parametrize("vaarallinen", ["_", "' OR 1=1 --", "'; DROP TABLE tuotteet; --"])
def test_sql_injektio_ei_vaikuta_kyselyyn(conn, vaarallinen):
    assert hae_tuotteet(vaarallinen, conn=conn) == []
    assert conn.execute("SELECT COUNT(*) FROM tuotteet").fetchone()[0] == 237


def test_prosenttimerkki_haetaan_kirjaimellisesti_eika_jokerina(conn):
    tulos = hae_tuotteet("%", raja=1000, conn=conn)
    assert 0 < len(tulos) < 237
    assert all("%" in t["nimi"] for t in tulos)


# --- listaa_kategoriat / listaa_ruokavaliot -------------------------------


def test_listaa_kategoriat(conn):
    kategoriat = listaa_kategoriat(conn=conn)
    assert len(kategoriat) == 15
    assert [k["nimi"] for k in kategoriat] == sorted(k["nimi"] for k in kategoriat)
    assert sum(k["tuotteita"] for k in kategoriat) == 237
    assert set(kategoriat[0]) == {"id", "nimi", "tuotteita"}


def test_listaa_ruokavaliot(conn):
    assert listaa_ruokavaliot(conn=conn) == [
        {"koodi": "gluteeniton", "nimi": "Gluteeniton", "tuotteita": 184},
        {"koodi": "laktoositon", "nimi": "Laktoositon", "tuotteita": 189},
        {"koodi": "vegaaninen", "nimi": "Vegaaninen", "tuotteita": 138},
    ]


# --- laske_hinta ----------------------------------------------------------


def test_laske_hinta_kappaleet_ja_paino(conn):
    maito, banaani = _id(conn, "Kevytmaito 1 l"), _id(conn, "Banaani")
    tulos = laske_hinta(
        [{"tuote_id": maito, "maara": 3}, {"tuote_id": banaani, "maara": 1.2}], conn=conn
    )
    assert [r["rivihinta_snt"] for r in tulos["rivit"]] == [327, 239]  # 3*109, 1.2*199=238.8
    assert tulos["yhteensa_snt"] == 566
    assert tulos["rivit"][0]["nimi"] == "Kevytmaito 1 l"


def test_pyoristys_puolikkaat_ylospain(conn):
    # Kiivi 349 snt/kg * 0.5 kg = 174.5 -> 175 (Pythonin round() pyöristäisi parilliseen: 174)
    kiivi = _id(conn, "Kiivi")
    assert laske_hinta([{"tuote_id": kiivi, "maara": 0.5}], conn=conn)["yhteensa_snt"] == 175


def test_tyhja_ostoslista(conn):
    assert laske_hinta([], conn=conn) == {"rivit": [], "yhteensa_snt": 0}


def test_sama_tuote_kahdesti_lasketaan_molemmat(conn):
    maito = _id(conn, "Kevytmaito 1 l")
    tulos = laske_hinta([{"tuote_id": maito, "maara": 1}] * 2, conn=conn)
    assert tulos["yhteensa_snt"] == 218


def test_tuntematon_tuote_on_virhe(conn):
    with pytest.raises(TyokaluVirhe, match="99999"):
        laske_hinta([{"tuote_id": 99999, "maara": 1}], conn=conn)


def test_kappaletuotteen_murtomaara_on_virhe(conn):
    with pytest.raises(TyokaluVirhe, match="kappaleittain"):
        laske_hinta([{"tuote_id": _id(conn, "Kevytmaito 1 l"), "maara": 1.5}], conn=conn)


@pytest.mark.parametrize(
    "ostoslista",
    [
        "maito",
        [{"maara": 1}],
        [{"tuote_id": "1", "maara": 1}],
        [{"tuote_id": 1, "maara": 0}],
        [{"tuote_id": 1, "maara": -2}],
        [{"tuote_id": 1, "maara": "2"}],
        [{"tuote_id": 1}],
    ],
)
def test_virheelliset_ostoslistat(conn, ostoslista):
    with pytest.raises(TyokaluVirhe):
        laske_hinta(ostoslista, conn=conn)
