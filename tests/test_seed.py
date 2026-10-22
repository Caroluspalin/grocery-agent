import sqlite3

import pytest

from ruokaostos import db
from scripts.seed import seed


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "test.db"
    seed(path)
    with db.connect(path) as c:
        yield c
    c.close()


def test_tuotteita_noin_200(conn):
    n = conn.execute("SELECT COUNT(*) FROM tuotteet").fetchone()[0]
    assert 180 <= n <= 250


def test_vegaaniset_ovat_laktoosittomia(conn):
    rikkojat = conn.execute(
        """SELECT t.nimi FROM tuotteet t
           JOIN tuote_ruokavalio tr ON tr.tuote_id = t.id
           JOIN ruokavaliot r ON r.id = tr.ruokavalio_id AND r.koodi = 'vegaaninen'
           WHERE t.id NOT IN (
               SELECT tr2.tuote_id FROM tuote_ruokavalio tr2
               JOIN ruokavaliot r2 ON r2.id = tr2.ruokavalio_id AND r2.koodi = 'laktoositon')"""
    ).fetchall()
    assert rikkojat == []


def test_viiteavaimet_pakotetaan(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO tuote_ruokavalio VALUES (99999, 1)")
