"""Nightly database snapshots — the difference between a bad bug and a
dead company.

Production is a single SQLite file on the Railway volume. This takes a
consistent snapshot every night (SQLite's online backup API — safe while
the app is running), keeps the last KEEP days alongside the live DB, and
logs the result so a silent failure is visible in the logs.

Limits, stated honestly: snapshots live on the SAME volume, so they
protect against corruption, bad migrations, and app bugs that mangle data
— the realistic failure modes — but not against the volume itself dying.
Off-site copies (S3 or similar) need credentials Rocco has to create;
until then this is the 95% answer that costs nothing.

Restore: stop the app, copy backups/adapix-YYYY-MM-DD.db over the live DB
file, start the app.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

KEEP = 7


def _live_db_path() -> Path | None:
    """The SQLite file behind DATABASE_URL, or None when not SQLite."""
    from .config import Settings
    url = Settings().database_url
    if not url.startswith("sqlite:///"):
        return None
    raw = url[len("sqlite:///"):]
    return Path(raw)


def run_nightly_backup() -> str:
    """One pass: snapshot if today's doesn't exist yet, prune old ones.
    Returns a short status string for logging. Never raises."""
    try:
        src = _live_db_path()
        if src is None or not src.exists():
            return "skipped: no sqlite db file"
        backup_dir = Path(os.environ.get("ADAPIX_VAR", ".")) / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.utcnow().strftime("%Y-%m-%d")
        dest = backup_dir / f"adapix-{today}.db"
        if dest.exists():
            return "skipped: today's snapshot already exists"

        # SQLite online backup API — consistent even mid-write.
        with sqlite3.connect(str(src)) as live, sqlite3.connect(str(dest)) as snap:
            live.backup(snap)

        # Sanity: a snapshot that can't count its own orgs is not a backup.
        with sqlite3.connect(str(dest)) as check:
            n_orgs = check.execute("SELECT COUNT(*) FROM organizations").fetchone()[0]
            n_msgs = check.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

        # Rotate: newest KEEP survive.
        snaps = sorted(backup_dir.glob("adapix-*.db"))
        for old in snaps[:-KEEP]:
            try:
                old.unlink()
            except Exception:
                pass

        size_mb = dest.stat().st_size / 1024 / 1024
        return (
            f"snapshot {dest.name} written ({size_mb:.1f} MB, "
            f"{n_orgs} orgs, {n_msgs} messages, {min(len(snaps), KEEP)} kept)"
        )
    except Exception as e:
        return f"FAILED: {e}"
