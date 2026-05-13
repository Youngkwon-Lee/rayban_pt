from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "storage" / "bridge.db"
SCHEMA_PATH = ROOT / "schema.sql"

DB_PATH.parent.mkdir(parents=True, exist_ok=True)

with sqlite3.connect(DB_PATH) as conn:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

print(f"Initialized DB at: {DB_PATH}")
