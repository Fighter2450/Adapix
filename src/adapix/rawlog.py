"""Size-capped raw event log — the never-lose-an-inbound safety net without
the unbounded growth.

Every inbound SMS and every call report is appended verbatim to a .jsonl on
the volume so a downstream bug can't cost us the message. Left unbounded
these files grow forever and accumulate PHI (call transcripts especially).
This caps each at MAX_BYTES: when it's exceeded, the current file is rotated
to `<name>.1` (one previous generation kept) and a fresh file started. So at
most ~2×MAX_BYTES per stream lives on disk at any time.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024   # 5 MB per stream, ×2 with the rotated copy


def append_raw(name: str, obj: dict) -> None:
    """Append one JSON record to $ADAPIX_VAR/<name>, rotating at MAX_BYTES.
    Never raises — raw logging must never break request handling."""
    try:
        path = Path(os.environ.get("ADAPIX_VAR", ".")) / name
        try:
            if path.exists() and path.stat().st_size >= MAX_BYTES:
                path.replace(path.with_suffix(path.suffix + ".1"))
        except Exception:
            pass
        record = {"t": int(time.time()), **obj}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass
