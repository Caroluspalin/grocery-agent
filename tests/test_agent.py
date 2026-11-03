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
    Agentti(client, conn, model="testimalli").kysy("x")
    p = client.pyynnot[0]
    assert p["model"] == "testimalli"
    assert p["messages"][0] == {"role": "system", "content": agent.JARJESTELMAPROMPTI}
    assert p["tools"] is agent.TYOKALUT
    assert p["max_completion_tokens"] == agent.MAX_TOKENS
    assert set(p) == {"model", "messages", "tools", "max_completion_tokens"}
