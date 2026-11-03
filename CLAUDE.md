# grocery-agent

AI-agentti, joka kokoaa ostoslistan käyttäjän ruokavalion ja budjetin mukaan.
(AI-agentit, työkalut, LLMOps).

## Rakenne
- `db/schema.sql` – SQLite-skeema: kategoriat, tuotteet, ruokavaliot, tuote_ruokavalio (N:M)
- `scripts/seed.py` – luo kannan alusta (237 tuotetta), ajettavissa uudelleen
- `ruokaostos/db.py` – yhteys, `PRAGMA foreign_keys = ON`, oma `casefold`-funktio
- `ruokaostos/tools.py` – agentin työkalut: hae_tuotteet, listaa_kategoriat, listaa_ruokavaliot, laske_hinta
- `ruokaostos/agent.py` – oma agenttisilmukka OpenAI Chat Completions -rajapinnalla (ei LangChainia) + komentorivi
- `tests/` – pytest, `conftest.py` luo väliaikaisen kannan

## Komennot
- Kanta: `.venv/bin/python scripts/seed.py`
- Testit: `.venv/bin/pytest`
- Agentti: `.venv/bin/python -m ruokaostos.agent`

## Säännöt
- Nimet suomeksi (funktiot, taulut, sarakkeet), commit-viestit englanniksi.
- Hinnat aina sentteinä kokonaislukuina (`hinta_snt`). Rahalaskut `Decimal`, puolikkaat ylöspäin.
- Vain parametrisoitua SQL:ää. Listat JSON-parametrina + `json_each()`. Ei koskaan SQL:ää merkkijonoista.
- Virheellinen syöte → `TyokaluVirhe` korjausohjeella, ei tyhjää tulosta.
- Ruokavaliomerkinnät varovaisesti: puuttuva merkintä = "ei varmistettu", ei "sopimaton".
- Agentti ei koskaan keksi tuotteita tai hintoja, kaikki tulee työkaluista.
- Funktioille voi antaa `conn`-parametrin testejä varten.
- Jokaiselle uudelle ominaisuudelle testit. Testit eivät kutsu oikeaa API:a.
- `.env` ei koskaan gittiin.

## Työtapa
- Opettelen samalla, joten selitä lyhyesti jokainen suunnittelupäätös ja miksi.
- Pidän jokaisesta vaiheesta päiväkirjaa, joten kerro lopuksi tärkeimmät opittavat asiat.
- Älä lisää isoja ominaisuuksia, joita en pyytänyt. Ehdota ne ensin.

## Tilanne
- Valmis: tietokanta, työkalut, agenttisilmukka (OpenAI SDK, Chat Completions), 62 testiä.
- Asetukset `.env`:ssä: `OPENAI_API_KEY`, `MODEL` (pakollinen, ei oletusta).
- Seuraavaksi: ensimmäinen oikea API-ajo, evalit (20–30 kysymystä + onnistumisprosentti), Langfuse-tracing, README + demo-GIF.
- Myöhemmin: RAG, frontend, kuittien luku.