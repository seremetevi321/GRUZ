"""Минимальный раннер SQL-миграций: файлы migrations/NNN_*.sql применяются по порядку один раз."""
from pathlib import Path

from .db import engine

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def migrate() -> list[str]:
    applied_now = []
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        cur.execute("SELECT pg_advisory_xact_lock(424242)")
        cur.execute("SELECT name FROM schema_migrations")
        done = {r[0] for r in cur.fetchall()}
        for f in sorted(MIGRATIONS.glob("*.sql")):
            if f.name in done:
                continue
            cur.execute(f.read_text(encoding="utf-8"))
            cur.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (f.name,))
            applied_now.append(f.name)
        raw.commit()
    finally:
        raw.close()
    return applied_now


if __name__ == "__main__":
    print("applied:", migrate())
