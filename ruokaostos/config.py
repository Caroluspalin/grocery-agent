"""Asetukset ympäristömuuttujista (.env ladataan automaattisesti)."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
DATABASE_PATH = PROJECT_ROOT / os.getenv("DATABASE_PATH", "data/ruokakauppa.db")
SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
