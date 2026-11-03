"""Agenttisilmukan testit valeasiakkaalla: API:a ei kutsuta."""

import json
from types import SimpleNamespace

import pytest

from ruokaostos import agent
from ruokaostos.agent import VIRHE_ETULIITE, Agentti, KierrosrajaYlittyi, aja_tyokalu


def tyokalukutsu(nimi, syote, id_="call_1"):
    """OpenAI:n tool_calls-alkio. Argumentit ovat aina JSON-merkkijono, ei dict."""
    argumentit = syote if isinstance(syote, str) else json.dumps(syote)
    return SimpleNamespace(id=id_, type="function", function=SimpleNamespace(name=nimi, arguments=argumentit))


def vastaus(finish_reason, teksti=None, *kutsut, refusal=None):
    """ChatCompletion-olion rakenne: choices[0].message ja finish_reason."""
    viesti = SimpleNamespace(role="assistant", content=teksti, tool_calls=list(kutsut) or None, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish_reason, message=viesti)], usage=None)


class ValeClient:
    """Palauttaa ennalta määrätyt vastaukset ja tallentaa pyynnöt."""

    def __init__(self, *vastaukset):
        self.vastaukset = list(vastaukset)
        self.pyynnot = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # Kopio, koska agentti jatkaa saman listan muokkaamista.
        self.pyynnot.append({**kwargs, "messages": list(kwargs["messages"])})
        if isinstance(self.vastaukset[0], Exception):
            raise self.vastaukset.pop(0)
        return self.vastaukset.pop(0) if len(self.vastaukset) > 1 else self.vastaukset[0]


def tool_viestit(pyynto):
    return [v for v in pyynto["messages"] if v["role"] == "tool"]


# --- työkalukuvaukset -----------------------------------------------------


def test_jokaisella_funktiolla_on_kuvaus():
    assert {t["function"]["name"] for t in agent.TYOKALUT} == set(agent._FUNKTIOT)
    for t in agent.TYOKALUT:
        assert t["type"] == "function"
        assert t["function"]["description"]
        assert t["function"]["parameters"]["type"] == "object"


def test_skeeman_parametrit_vastaavat_funktiota():
    import inspect

    for t in agent.TYOKALUT:
        f = t["function"]
        parametrit = set(inspect.signature(agent._FUNKTIOT[f["name"]]).parameters) - {"conn"}
        assert set(f["parameters"]["properties"]) == parametrit, f["name"]


def test_ruokavalioiden_enum_vastaa_tietokantaa(conn):
    [hae] = [t["function"] for t in agent.TYOKALUT if t["function"]["name"] == "hae_tuotteet"]
    enum = hae["parameters"]["properties"]["ruokavaliot"]["items"]["enum"]
    assert set(enum) == {r["koodi"] for r in agent.tools.listaa_ruokavaliot(conn=conn)}


# --- aja_tyokalu ----------------------------------------------------------


def test_aja_tyokalu_palauttaa_jsonin(conn):
    tulos = aja_tyokalu("hae_tuotteet", '{"hakusana": "banaani"}', conn)
    assert json.loads(tulos)[0]["nimi"] == "Banaani"


def test_tyhjat_argumentit_ovat_tyhja_objekti(conn):
    tulos = aja_tyokalu("listaa_kategoriat", "", conn)
    assert not tulos.startswith(VIRHE_ETULIITE)


def test_tyokaluvirhe_on_virheteksti(conn):
    tulos = aja_tyokalu("hae_tuotteet", '{"hakusana": "", "kategoria": "Autot"}', conn)
    assert tulos.startswith(VIRHE_ETULIITE)
    assert "listaa_kategoriat" in tulos


def test_rikkinainen_json_on_virheteksti(conn):
    tulos = aja_tyokalu("hae_tuotteet", '{"hakusana": "maito"', conn)
    assert tulos.startswith(VIRHE_ETULIITE)
    assert "JSON" in tulos


def test_json_joka_ei_ole_objekti_on_virheteksti(conn):
    tulos = aja_tyokalu("hae_tuotteet", '["maito"]', conn)
    assert tulos.startswith(VIRHE_ETULIITE)


def test_tuntematon_parametri_on_virheteksti(conn):
    assert aja_tyokalu("listaa_kategoriat", '{"jarjestys": "nimi"}', conn).startswith(VIRHE_ETULIITE)


def test_tuntematon_tyokalu_on_virheteksti(conn):
    assert aja_tyokalu("poista_kaikki", "{}", conn).startswith(VIRHE_ETULIITE)


def test_odottamaton_poikkeus_ei_kaada_silmukkaa(conn, monkeypatch):
    def rikki(**_):
        raise ZeroDivisionError

    monkeypatch.setitem(agent._FUNKTIOT, "listaa_kategoriat", rikki)
    tulos = aja_tyokalu("listaa_kategoriat", "{}", conn)
    assert tulos.startswith(VIRHE_ETULIITE)
    assert "ZeroDivisionError" not in tulos


# --- silmukka -------------------------------------------------------------


def test_suora_vastaus_ilman_tyokaluja(conn):
    client = ValeClient(vastaus("stop", "Hei!"))
    assert Agentti(client, conn).kysy("moi") == "Hei!"
    assert len(client.pyynnot) == 1


def test_tyokalukierros_ja_lopullinen_vastaus(conn):
    client = ValeClient(
        vastaus("tool_calls", None, tyokalukutsu("hae_tuotteet", {"hakusana": "banaani"})),
        vastaus("stop", "Banaani maksaa 1,99 €/kg."),
    )
    a = Agentti(client, conn)
    assert a.kysy("paljonko banaani?") == "Banaani maksaa 1,99 €/kg."

    [tool] = tool_viestit(client.pyynnot[1])
    assert tool["tool_call_id"] == "call_1"
    assert json.loads(tool["content"])[0]["hinta_snt"] == 199
    assert [v["role"] for v in a.viestit] == ["system", "user", "assistant", "tool", "assistant"]


def test_assistentin_tyokalukutsut_tallennetaan_historiaan(conn):
    client = ValeClient(
        vastaus("tool_calls", None, tyokalukutsu("listaa_kategoriat", {})),
        vastaus("stop", "ok"),
    )
    a = Agentti(client, conn)
    a.kysy("x")
    [kutsu] = a.viestit[2]["tool_calls"]
    assert kutsu == {"id": "call_1", "type": "function", "function": {"name": "listaa_kategoriat", "arguments": "{}"}}


def test_rinnakkaiset_kutsut_saavat_omat_tool_viestit(conn):
    client = ValeClient(
        vastaus(
            "tool_calls",
            None,
            tyokalukutsu("hae_tuotteet", {"hakusana": "maito"}, "a"),
            tyokalukutsu("listaa_kategoriat", {}, "b"),
        ),
        vastaus("stop", "ok"),
    )
    Agentti(client, conn).kysy("x")
    assert [t["tool_call_id"] for t in tool_viestit(client.pyynnot[1])] == ["a", "b"]


def test_tyokaluvirhe_valitetaan_mallille(conn):
    client = ValeClient(
        vastaus("tool_calls", None, tyokalukutsu("laske_hinta", {"ostoslista": [{"tuote_id": 99999, "maara": 1}]})),
        vastaus("stop", "Tuotetta ei löytynyt."),
    )
    Agentti(client, conn).kysy("x")
    [tool] = tool_viestit(client.pyynnot[1])
    assert tool["content"].startswith(VIRHE_ETULIITE)
    assert "99999" in tool["content"]


def test_rikkinainen_json_valitetaan_mallille_eika_kaada(conn):
    client = ValeClient(
        vastaus("tool_calls", None, tyokalukutsu("hae_tuotteet", "{hakusana: maito}")),
        vastaus("stop", "ok"),
    )
    assert Agentti(client, conn).kysy("x") == "ok"
    [tool] = tool_viestit(client.pyynnot[1])
    assert "JSON" in tool["content"]


def test_kierrosraja_pysayttaa_silmukan_ja_palauttaa_historian(conn):
    client = ValeClient(vastaus("tool_calls", None, tyokalukutsu("listaa_kategoriat", {})))
    a = Agentti(client, conn)
    with pytest.raises(KierrosrajaYlittyi):
        a.kysy("x")
    assert len(client.pyynnot) == agent.MAX_KIERROKSET
    assert [v["role"] for v in a.viestit] == ["system"]


def test_historia_sailyy_viestien_valilla(conn):
    client = ValeClient(vastaus("stop", "1"), vastaus("stop", "2"))
    a = Agentti(client, conn)
    a.kysy("eka")
    a.kysy("toka")
    assert [v["content"] for v in client.pyynnot[1]["messages"] if v["role"] == "user"] == ["eka", "toka"]


def test_api_virhe_palauttaa_historian(conn):
    client = ValeClient(
        vastaus("stop", "1"),
        vastaus("tool_calls", None, tyokalukutsu("listaa_kategoriat", {})),
        ConnectionError("verkko"),
    )
    a = Agentti(client, conn)
    a.kysy("eka")
    with pytest.raises(ConnectionError):
        a.kysy("toka")
    assert [v["role"] for v in a.viestit] == ["system", "user", "assistant"]


def test_refusal(conn):
    client = ValeClient(vastaus("stop", None, refusal="I can't help with that."))
    assert "en voi auttaa" in Agentti(client, conn).kysy("x")


def test_content_filter(conn):
    client = ValeClient(vastaus("content_filter"))
    assert "en voi auttaa" in Agentti(client, conn).kysy("x")


def test_length_nostaa_virheen_ja_palauttaa_historian(conn):
    client = ValeClient(vastaus("length", "Ostoslista: maito, lei"))
    a = Agentti(client, conn)
    with pytest.raises(RuntimeError, match="max_completion_tokens"):
        a.kysy("x")
    assert [v["role"] for v in a.viestit] == ["system"]


def test_pyynnon_asetukset(conn):
    client = ValeClient(vastaus("stop", "ok"))
    # reasoning_effort annetaan, jotta testi ei riipu käyttäjän .env-tiedostosta.
    Agentti(client, conn, model="testimalli", reasoning_effort="").kysy("x")
    p = client.pyynnot[0]
    assert p["model"] == "testimalli"
    assert p["messages"][0] == {"role": "system", "content": agent.JARJESTELMAPROMPTI}
    assert p["tools"] is agent.TYOKALUT
    assert p["max_completion_tokens"] == agent.MAX_TOKENS
    assert set(p) == {"model", "messages", "tools", "max_completion_tokens"}


# --- hakuohjeet kuvauksissa ja promptissa ---------------------------------
# Oikeassa ajossa malli laittoi ruokavalion hakusanaan ("juusto gluteeniton") eikä
# käyttänyt kategorioita. Nämä testit varmistavat, ettei ohjeistus katoa vahingossa.


def _hae_tuotteet_kuvaus():
    [hae] = [t["function"] for t in agent.TYOKALUT if t["function"]["name"] == "hae_tuotteet"]
    return hae


def test_hae_tuotteet_ohjaa_ruokavalion_parametriin():
    hae = _hae_tuotteet_kuvaus()
    props = hae["parameters"]["properties"]
    assert "ruokavaliota ei koskaan laiteta hakusanaan" in hae["description"].lower()
    assert "ei ruokavalioita" in props["hakusana"]["description"].lower()
    assert "ei koskaan hakusanassa" in props["ruokavaliot"]["description"].lower()


def test_hae_tuotteet_ohjaa_kategoriahakuun():
    hae = _hae_tuotteet_kuvaus()
    props = hae["parameters"]["properties"]
    assert "kategoria-parametrilla ja tyhjällä hakusanalla" in hae["description"]
    assert "yksi perusmuotoinen sana" in props["hakusana"]["description"].lower()
    assert "ensisijainen" in props["kategoria"]["description"].lower()


def test_prompti_ohjaa_kategorioihin_ja_varahakuun():
    p = agent.JARJESTELMAPROMPTI
    assert "kutsu ensin listaa_kategoriat" in p
    assert "Älä koskaan laita ruokavaliota hakusanaan" in p
    assert "ennen kuin kerrot, ettei tuotetta ole" in p


def test_promptin_esimerkki_pitaa_paikkansa(conn):
    """Prompti väittää, ettei 'juusto' löydä Edamia mutta kategoria löytää."""
    assert "Edam viipale 300 g" in agent.JARJESTELMAPROMPTI
    nimet = lambda tulos: {t["nimi"] for t in tulos}
    assert "Edam viipale 300 g" not in nimet(agent.tools.hae_tuotteet("juusto", conn=conn))
    assert "Edam viipale 300 g" in nimet(agent.tools.hae_tuotteet("", kategoria="Juustot", conn=conn))


# --- budjetti -------------------------------------------------------------


def _laske_hinta_kuvaus():
    [laske] = [t["function"] for t in agent.TYOKALUT if t["function"]["name"] == "laske_hinta"]
    return laske


def test_laske_hinta_skeemassa_valinnainen_budjetti():
    laske = _laske_hinta_kuvaus()
    budjetti = laske["parameters"]["properties"]["budjetti_snt"]
    assert budjetti["type"] == "integer" and budjetti["minimum"] == 0
    assert laske["parameters"]["required"] == ["ostoslista"]
    assert "budjetti_snt-parametrina" in laske["description"]


def test_prompti_ohjaa_budjetin_kayttoon():
    p = agent.JARJESTELMAPROMPTI
    assert "anna se aina laske_hinta-kutsuun" in p
    assert "budjetti_ylittyy: true" in p
    assert "kutsu laske_hinta uudelleen" in p
    assert "ylitys_snt" in p and "Älä laske erotusta itse" in p
    assert "esitä halvin mahdollinen lista" in p


def test_prompti_kieltaa_sopivuusvaitteet_tuntemattomille_ruokavalioille():
    """Turvallisuus: eval-ajossa agentti väitti aamupalaa pähkinättömäksi ilman tietoa."""
    p = agent.JARJESTELMAPROMPTI
    assert "Tuetut ruokavaliot ovat vain ne, jotka listaa_ruokavaliot palauttaa" in p
    assert "pähkinäallergia" in p
    assert "Älä silloin koskaan väitä tuotteita sille sopiviksi" in p
    assert "pakkausseloste" in p
    assert "Voit silti näyttää tuotteita" in p


def test_prompti_ohjaa_tekemaan_ehdotuksen_heti():
    p = agent.JARJESTELMAPROMPTI
    assert "tee ostoslistaehdotus heti ja kysy toiveita vasta sen jälkeen" in p
    assert "Kysy tarkennusta ensin vain" in p


def test_budjetti_valitetaan_tyokalulle_ja_tulos_mallille(conn):
    lohi = conn.execute("SELECT id FROM tuotteet WHERE nimi = 'Lohifilee'").fetchone()[0]
    client = ValeClient(
        vastaus(
            "tool_calls",
            None,
            tyokalukutsu("laske_hinta", {"ostoslista": [{"tuote_id": lohi, "maara": 1}], "budjetti_snt": 300}),
        ),
        vastaus("stop", "Budjetti ylittyy 16,99 €."),
    )
    Agentti(client, conn).kysy("kilo lohta, budjetti 3 €")
    [tool] = tool_viestit(client.pyynnot[1])
    tulos = json.loads(tool["content"])
    assert tulos["budjetti_ylittyy"] is True
    assert tulos["ylitys_snt"] == 1699


# --- reasoning_effort -----------------------------------------------------


def test_reasoning_effort_valitetaan_kun_annettu(conn):
    client = ValeClient(vastaus("stop", "ok"))
    Agentti(client, conn, model="m", reasoning_effort="none").kysy("x")
    assert client.pyynnot[0]["reasoning_effort"] == "none"


def test_reasoning_effort_puuttuu_kun_tyhja(conn):
    client = ValeClient(vastaus("stop", "ok"))
    Agentti(client, conn, model="m", reasoning_effort="").kysy("x")
    assert "reasoning_effort" not in client.pyynnot[0]
