import pytest

from ruokaostos import db
from scripts.seed import seed


@pytest.fixture(scope="session")
def seeded_db_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("db") / "test.db"
    seed(path)
    return path


@pytest.fixture
def conn(seeded_db_path):
    c = db.connect(seeded_db_path)
    yield c
    c.close()
