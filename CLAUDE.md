# grocery-agent

AI-agentti, joka kokoaa ostoslistan käyttäjän ruokavalion ja budjetin mukaan.
(AI-agentit, työkalut, LLMOps).

## Rakenne
- `db/schema.sql` – SQLite-skeema: kategoriat, tuotteet, ruokavaliot, tuote_ruokavalio (N:M)
- `scripts/seed.py` – luo kannan alusta (237 tuotetta), ajettavissa uudelleen
- `ruokaostos/db.py` – yhteys, `PRAGMA foreign_keys = ON`, oma `casefold`-funktio
- `ruokaostos/tools.py` – agentin työkalut: hae_tuotteet, listaa_kategoriat, listaa_ruokavaliot, laske_hinta (valinnainen `budjetti_snt` → `budjetti_ylittyy`, `ylitys_snt`/`jaljella_snt`)
- `ruokaostos/agent.py` – oma agenttisilmukka OpenAI Chat Completions -rajapinnalla (ei LangChainia) + komentorivi
- `tests/` – pytest, `conftest.py` luo väliaikaisen kannan
- `evals/` – evalit oikeaa API:a vasten: `tapaukset.json` (25 tapausta), `tarkistukset.py` (koodilla tehtävät tarkistukset, 3 yleistä + tapauskohtaiset), `aja.py` (ajaja), `tulokset/` (aikaleimatut JSON-tulokset, gitissä)

## Komennot
- Kanta: `.venv/bin/python scripts/seed.py`
- Testit: `.venv/bin/pytest`
- Agentti: `.venv/bin/python -m ruokaostos.agent`
- Evalit (maksaa, ajan itse): `.venv/bin/python -m evals.aja` (`--vain ID...`, `--malli`, `--kertaa N`, `-v`)

## Säännöt
- Nimet suomeksi (funktiot, taulut, sarakkeet), commit-viestit englanniksi.
- Hinnat aina sentteinä kokonaislukuina (`hinta_snt`). Rahalaskut `Decimal`, puolikkaat ylöspäin.
- Vain parametrisoitua SQL:ää. Listat JSON-parametrina + `json_each()`. Ei koskaan SQL:ää merkkijonoista.
- Virheellinen syöte → `TyokaluVirhe` korjausohjeella, ei tyhjää tulosta.
- Eval-ajajassa API-virheet (tila `virhe`) erotetaan epäonnistumisista: ei tarkistuksia, ei mukana prosentissa. 3 ensimmäistä samaa API-virhettä keskeyttää ajon.
- Ruokavaliomerkinnät varovaisesti: puuttuva merkintä = "ei varmistettu", ei "sopimaton".
- Agentti ei koskaan keksi tuotteita tai hintoja, kaikki tulee työkaluista. Budjettivertailu tehdään koodissa (`laske_hinta`), ei mallin päässälaskuna.
- Turvallisuus: allergioista ja ruokavalioista, joita `listaa_ruokavaliot` ei palauta, agentti ei koskaan väitä tuotteita sopiviksi. Kehottaa tarkistamaan pakkausselosteen.
- Funktioille voi antaa `conn`-parametrin testejä varten.
- Jokaiselle uudelle ominaisuudelle testit. Testit eivät kutsu oikeaa API:a.
- Evalit eivät ole osa pytestiä. Claude ei aja evaleja, käyttäjä ajaa ne. Tarkistukset tehdään koodilla jäljestä (työkalukutsut ja tulokset), ei LLM-tuomarilla.
- Agentin käytöstä ei korjata promptiin käsin ilman, että evalit näyttävät ongelman ja korjauksen vaikutuksen.
- Malli ei ole deterministinen: johtopäätökset vähintään 2–3 ajosta (`--kertaa`), ei yhdestä.
- Kun tarkistuksia muutetaan, aiemmat ajot pisteytetään uudelleen ilman API:a, jotta näkyy, muuttuiko tulos tarkistuksen vai agentin takia.
- Agenttiin vaikuttavat muutokset commitoidaan vasta, kun käyttäjä on ajanut evalit. Tulostiedostot omana committinaan (`eval: ...`).
- `.env` ei koskaan gittiin.

## Työtapa
- Opettelen samalla, joten selitä lyhyesti jokainen suunnittelupäätös ja miksi.
- Pidän jokaisesta vaiheesta päiväkirjaa, joten kerro lopuksi tärkeimmät opittavat asiat.
- Älä lisää isoja ominaisuuksia, joita en pyytänyt. Ehdota ne ensin.

## Tilanne
- Valmis: tietokanta, työkalut, agenttisilmukka (OpenAI SDK, Chat Completions), hakuohjeet, budjetti `laske_hinta`-työkalussa, allergiasääntö, evalit (25 tapausta, `--kertaa`, API-virheiden erottelu), 126 testiä.
- Asetukset `.env`:ssä: `OPENAI_API_KEY`, `MODEL` (pakollinen, ei oletusta), `REASONING_EFFORT` (valinnainen; tyhjä = ei lähetetä).
- Eval-tulokset gpt-4.1-mini: lähtötaso 72 %; budjettikorjauksen ja "ylit"-muutoksen jälkeen 3 ajoa 72/76/80 %, keskiarvo 76 %.
- Epäonnistuu joka kerta (3/3): `aamupala_gluteeniton_10e` (vain Aamupala-kategoria), `opiskelijan_viikko` ja `mahdoton_budjetti_perhe` (kysyy toiveita eikä tee halvinta listaa).
- Vaihtelee: `tuote_puuttuu_hummeri`, `laktoositon_taco`, `grilli_ilta`, `mahdoton_budjetti_lohi` (1/3), `kaksi_ruokavaliota` (2/3).
- gpt-6-luna: 3 ajoa kaatui kokonaan API-virheeseen (Chat Completions ei salli työkaluja + reasoning_effort). Seuraava yritys `REASONING_EFFORT=none`.
- Tunnettu aukko tarkistuksissa: sopivuusväitettä ("pähkinätön aamupala") ei tunnisteta sanalistalla. Ehdokas ensimmäiseksi LLM-tuomariksi.
- Seuraavaksi: korjaukset joka kerta epäonnistuviin tapauksiin evalien ohjaamana, Langfuse-tracing, README + demo-GIF.
- Myöhemmin: RAG, frontend, kuittien luku.