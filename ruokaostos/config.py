"""Asetukset ympäristömuuttujista (.env ladataan automaattisesti)."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# Ei oletusmallia: malli valitaan aina tietoisesti .env-tiedostossa.
MODEL = os.getenv("MODEL", "")
DATABASE_PATH = PROJECT_ROOT / os.getenv("DATABASE_PATH", "data/ruokakauppa.db")
SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
