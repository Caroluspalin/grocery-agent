"""Agenttisilmukan testit valekäyttöliittymällä: API:a ei kutsuta."""

import json
from types import SimpleNamespace

import pytest

from ruokaostos import agent
from ruokaostos.agent import Agentti, KierrosrajaYlittyi, aja_tyokalu


def teksti(t):
    return SimpleNamespace(type="text", text=t)


def tyokalukutsu(nimi, syote, id_="tu_1"):
    return SimpleNamespace(type="tool_use", id=id_, name=nimi, input=syote)


def vastaus(stop_reason, *content):
    return SimpleNamespace(stop_reason=stop_reason, content=list(content), usage=None)


class ValeClient:
    """Palauttaa ennalta määrätyt vastaukset ja tallentaa pyynnöt."""

    def __init__(self, *vastaukset):
        self.vastaukset = list(vastaukset)
        self.pyynnot = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # Kopio, koska agentti jatkaa saman listan muokkaamista.
        self.pyynnot.append({**kwargs, "messages": list(kwargs["messages"])})
        if isinstance(self.vastaukset[0], Exception):
            raise self.vastaukset.pop(0)
        return self.vastaukset.pop(0) if len(self.vastaukset) > 1 else self.vastaukset[0]


# --- työkalukuvaukset -----------------------------------------------------


def test_jokaisella_funktiolla_on_kuvaus():
    assert {t["name"] for t in agent.TYOKALUT} == set(agent._FUNKTIOT)
    for t in agent.TYOKALUT:
        assert t["description"]
        assert t["input_schema"]["type"] == "object"


def test_skeeman_parametrit_vastaavat_funktiota():
    import inspect

    for t in agent.TYOKALUT:
        parametrit = set(inspect.signature(agent._FUNKTIOT[t["name"]]).parameters) - {"conn"}
        assert set(t["input_schema"]["properties"]) == parametrit, t["name"]


def test_ruokavalioiden_enum_vastaa_tietokantaa(conn):
    [hae] = [t for t in agent.TYOKALUT if t["name"] == "hae_tuotteet"]
    enum = hae["input_schema"]["properties"]["ruokavaliot"]["items"]["enum"]
    assert set(enum) == {r["koodi"] for r in agent.tools.listaa_ruokavaliot(conn=conn)}


# --- aja_tyokalu ----------------------------------------------------------


def test_aja_tyokalu_palauttaa_jsonin(conn):
    tulos = aja_tyokalu("hae_tuotteet", {"hakusana": "banaani"}, conn)
    assert "is_error" not in tulos
    assert json.loads(tulos["content"])[0]["nimi"] == "Banaani"


def test_tyokaluvirhe_on_is_error(conn):
    tulos = aja_tyokalu("hae_tuotteet", {"hakusana": "", "kategoria": "Autot"}, conn)
    assert tulos["is_error"] is True
    assert "listaa_kategoriat" in tulos["content"]


def test_tuntematon_parametri_on_is_error(conn):
    tulos = aja_tyokalu("listaa_kategoriat", {"jarjestys": "nimi"}, conn)
    assert tulos["is_error"] is True


def test_tuntematon_tyokalu_on_is_error(conn):
    assert aja_tyokalu("poista_kaikki", {}, conn)["is_error"] is True


def test_odottamaton_poikkeus_ei_kaada_silmukkaa(conn, monkeypatch):
    def rikki(**_):
        raise ZeroDivisionError

    monkeypatch.setitem(agent._FUNKTIOT, "listaa_kategoriat", rikki)
    tulos = aja_tyokalu("listaa_kategoriat", {}, conn)
    assert tulos["is_error"] is True
    assert "ZeroDivisionError" not in tulos["content"]


# --- silmukka -------------------------------------------------------------


def test_suora_vastaus_ilman_tyokaluja(conn):
    client = ValeClient(vastaus("end_turn", teksti("Hei!")))
    assert Agentti(client, conn).kysy("moi") == "Hei!"
    assert len(client.pyynnot) == 1


def test_tyokalukierros_ja_lopullinen_vastaus(conn):
    client = ValeClient(
        vastaus("tool_use", tyokalukutsu("hae_tuotteet", {"hakusana": "banaani"})),
        vastaus("end_turn", teksti("Banaani maksaa 1,99 €/kg.")),
    )
    a = Agentti(client, conn)
    assert a.kysy("paljonko banaani?") == "Banaani maksaa 1,99 €/kg."

    [tool_result] = client.pyynnot[1]["messages"][-1]["content"]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "tu_1"
    assert json.loads(tool_result["content"])[0]["hinta_snt"] == 199
    assert [v["role"] for v in a.viestit] == ["user", "assistant", "user", "assistant"]


def test_rinnakkaiset_kutsut_palautetaan_yhdessa_viestissa(conn):
    client = ValeClient(
        vastaus(
            "tool_use",
            tyokalukutsu("hae_tuotteet", {"hakusana": "maito"}, "a"),
            tyokalukutsu("listaa_kategoriat", {}, "b"),
        ),
        vastaus("end_turn", teksti("ok")),
    )
    Agentti(client, conn).kysy("x")
    tulokset = client.pyynnot[1]["messages"][-1]["content"]
    assert [t["tool_use_id"] for t in tulokset] == ["a", "b"]


def test_tyokaluvirhe_valitetaan_mallille(conn):
    client = ValeClient(
        vastaus("tool_use", tyokalukutsu("laske_hinta", {"ostoslista": [{"tuote_id": 99999, "maara": 1}]})),
        vastaus("end_turn", teksti("Tuotetta ei löytynyt.")),
    )
    Agentti(client, conn).kysy("x")
    [tool_result] = client.pyynnot[1]["messages"][-1]["content"]
    assert tool_result["is_error"] is True
    assert "99999" in tool_result["content"]


def test_kierrosraja_pysayttaa_silmukan_ja_palauttaa_historian(conn):
    client = ValeClient(vastaus("tool_use", tyokalukutsu("listaa_kategoriat", {})))
    a = Agentti(client, conn)
    with pytest.raises(KierrosrajaYlittyi):
        a.kysy("x")
    assert len(client.pyynnot) == agent.MAX_KIERROKSET
    assert a.viestit == []


def test_historia_sailyy_viestien_valilla(conn):
    client = ValeClient(vastaus("end_turn", teksti("1")), vastaus("end_turn", teksti("2")))
    a = Agentti(client, conn)
    a.kysy("eka")
    a.kysy("toka")
    assert [v["content"] for v in client.pyynnot[1]["messages"] if v["role"] == "user"] == ["eka", "toka"]


def test_api_virhe_palauttaa_historian(conn):
    client = ValeClient(
        vastaus("end_turn", teksti("1")),
        vastaus("tool_use", tyokalukutsu("listaa_kategoriat", {})),
        ConnectionError("verkko"),
    )
    a = Agentti(client, conn)
    a.kysy("eka")
    with pytest.raises(ConnectionError):
        a.kysy("toka")
    assert len(a.viestit) == 2


def test_refusal(conn):
    client = ValeClient(vastaus("refusal"))
    assert "en voi auttaa" in Agentti(client, conn).kysy("x")


def test_pyynnon_asetukset(conn):
    client = ValeClient(vastaus("end_turn", teksti("ok")))
    Agentti(client, conn, model="testimalli").kysy("x")
    p = client.pyynnot[0]
    assert p["model"] == "testimalli"
    assert p["system"] == agent.JARJESTELMAPROMPTI
    assert p["tools"] is agent.TYOKALUT
    assert p["cache_control"] == {"type": "ephemeral"}
    assert p["fallbacks"] == "default"
