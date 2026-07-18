"""Nightly database backups — the difference between a bad bug and a
dead company.

Two modes, picked automatically from DATABASE_URL:

- SQLite (local/dev): consistent file snapshot via SQLite's online backup
  API, kept alongside the live DB.
- Postgres (production on Railway): a logical dump — every table's rows
  serialized to a gzipped JSON file on the persistent volume. No pg_dump
  binary needed; the DB is small enough that SELECT * is fine for years.

Either way: one backup per UTC day, last KEEP kept, result surfaced in
logs and the newest filename in /health so freshness is checkable from
outside.

Limits, stated honestly: backups live on the app volume, so they protect
against corruption, bad migrations, and app bugs that mangle data — the
realistic failure modes — but not the volume itself dying. Off-site
copies need credentials Rocco has to create.

Restore (postgres): the JSON holds {table: [row dicts]} — reinsert with
SQLAlchemy after truncating, oldest tables first (organizations, users,
patients, campaigns, messages, ...).
"""
from __future__ import annotations

import gzip
import json
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

KEEP = 7


def _backup_dir() -> Path:
    d = Path(os.environ.get("ADAPIX_VAR", ".")) / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def latest_backup_name() -> str | None:
    snaps = sorted(_backup_dir().glob("adapix-*"))
    return snaps[-1].name if snaps else None


def _rotate() -> int:
    snaps = sorted(_backup_dir().glob("adapix-*"))
    for old in snaps[:-KEEP]:
        try:
            old.unlink()
        except Exception:
            pass
    return min(len(snaps), KEEP)


def _json_default(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v)


def run_nightly_backup() -> str:
    """One pass: back up if today's file doesn't exist yet, prune old ones.
    Returns a short status string for logging. Never raises."""
    try:
        from .config import Settings
        url = Settings().database_url
        today = datetime.utcnow().strftime("%Y-%m-%d")

        if url.startswith("sqlite:///"):
            src = Path(url[len("sqlite:///"):])
            if not src.exists():
                return "skipped: no sqlite db file"
            dest = _backup_dir() / f"adapix-{today}.db"
            if dest.exists():
                return "skipped: today's snapshot already exists"
            with sqlite3.connect(str(src)) as live, sqlite3.connect(str(dest)) as snap:
                live.backup(snap)
            with sqlite3.connect(str(dest)) as check:
                n_orgs = check.execute("SELECT COUNT(*) FROM organizations").fetchone()[0]
            kept = _rotate()
            mb = dest.stat().st_size / 1024 / 1024
            return f"sqlite snapshot {dest.name} ({mb:.1f} MB, {n_orgs} orgs, {kept} kept)"

        # Postgres (or anything else SQLAlchemy can read): logical dump.
        dest = _backup_dir() / f"adapix-{today}.json.gz"
        if dest.exists():
            return "skipped: today's dump already exists"
        from sqlalchemy import MetaData, select
        from .db import get_engine
        engine = get_engine()
        meta = MetaData()
        meta.reflect(bind=engine)
        dump: dict[str, list[dict]] = {}
        with engine.connect() as conn:
            for name, table in meta.tables.items():
                rows = conn.execute(select(table)).mappings().all()
                dump[name] = [dict(r) for r in rows]
        payload = json.dumps(dump, default=_json_default).encode("utf-8")
        tmp = dest.with_suffix(".tmp")
        with gzip.open(tmp, "wb") as f:
            f.write(payload)
        tmp.replace(dest)

        # Sanity: the file must decompress and contain the org table.
        with gzip.open(dest, "rb") as f:
            back = json.loads(f.read())
        n_orgs = len(back.get("organizations") or [])
        n_msgs = len(back.get("messages") or [])
        kept = _rotate()
        mb = dest.stat().st_size / 1024 / 1024
        return (
            f"pg dump {dest.name} ({mb:.1f} MB, {len(dump)} tables, "
            f"{n_orgs} orgs, {n_msgs} messages, {kept} kept)"
        )
    except Exception as e:
        return f"FAILED: {e}"
