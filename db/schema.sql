-- Ruokaostos-agentin tietokantaskeema (SQLite)
-- Huom: SQLite ei pakota viiteavaimia oletuksena, vaan jokaisen yhteyden
-- pitää ajaa PRAGMA foreign_keys = ON (ks. ruokaostos/db.py).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS kategoriat (
    id    INTEGER PRIMARY KEY,
    nimi  TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS tuotteet (
    id             INTEGER PRIMARY KEY,
    nimi           TEXT    NOT NULL UNIQUE,
    kategoria_id   INTEGER NOT NULL REFERENCES kategoriat(id) ON DELETE RESTRICT,
    -- Hinta senteissä kokonaislukuna: ei liukulukujen pyöristysvirheitä.
    hinta_snt      INTEGER NOT NULL CHECK (hinta_snt >= 0),
    -- Yksikkö, jota hinta koskee: kpl = pakkaus/kappale, kg/l = irtomyynti.
    yksikko        TEXT    NOT NULL CHECK (yksikko IN ('kpl', 'kg', 'l'))
);

CREATE INDEX IF NOT EXISTS idx_tuotteet_kategoria ON tuotteet(kategoria_id);

CREATE TABLE IF NOT EXISTS ruokavaliot (
    id     INTEGER PRIMARY KEY,
    koodi  TEXT NOT NULL UNIQUE,   -- koneluettava tunniste agentin työkaluille
    nimi   TEXT NOT NULL UNIQUE    -- ihmisluettava nimi
);

-- Many-to-many: tuotteella voi olla useita ruokavaliomerkintöjä ja päinvastoin.
CREATE TABLE IF NOT EXISTS tuote_ruokavalio (
    tuote_id       INTEGER NOT NULL REFERENCES tuotteet(id)    ON DELETE CASCADE,
    ruokavalio_id  INTEGER NOT NULL REFERENCES ruokavaliot(id) ON DELETE CASCADE,
    PRIMARY KEY (tuote_id, ruokavalio_id)
) WITHOUT ROWID;

-- Pääavain kattaa haut tuote_id:llä; tämä indeksi kattaa käänteisen haun
-- ("kaikki vegaaniset tuotteet").
CREATE INDEX IF NOT EXISTS idx_tuote_ruokavalio_ruokavalio
    ON tuote_ruokavalio(ruokavalio_id, tuote_id);

-- Ruokavaliot ovat osa skeemaa, eivät seed-dataa: koodi luottaa niiden olemassaoloon.
INSERT OR IGNORE INTO ruokavaliot (koodi, nimi) VALUES
    ('gluteeniton', 'Gluteeniton'),
    ('laktoositon', 'Laktoositon'),
    ('vegaaninen',  'Vegaaninen');
