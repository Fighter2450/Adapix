"""Founder alerting for server trouble — errors should be an email, not a
customer complaint three days later.

record_error(source) is called wherever the app catches something going
wrong at the request/loop level. When one source produces ERROR_THRESHOLD
errors inside WINDOW_MIN minutes, Rocco gets ONE email (per source, per
COOLDOWN_H hours) with the recent error lines attached. In-memory state:
a restart resets counters, which is fine — a crash-loop that survives
restarts will re-trip the threshold in minutes anyway.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque

ERROR_THRESHOLD = 10          # errors from one source...
WINDOW_MIN = 10               # ...within this many minutes
COOLDOWN_H = 6                # at most one email per source per this many hours

_lock = threading.Lock()
_errors: dict[str, deque] = {}          # source -> deque[(monotonic_ts, text)]
_last_alert: dict[str, float] = {}      # source -> monotonic ts


def record_error(source: str, detail: str = "") -> None:
    """Count one error for `source`; email the founder if it's a burst.
    Never raises — an alerting failure must not worsen the original one."""
    try:
        now = time.monotonic()
        send, lines = False, []
        with _lock:
            win = _errors.setdefault(source, deque(maxlen=50))
            win.append((now, (detail or "")[:300]))
            recent = [(t, d) for (t, d) in win if now - t <= WINDOW_MIN * 60]
            cooled = now - _last_alert.get(source, -1e12) >= COOLDOWN_H * 3600
            if len(recent) >= ERROR_THRESHOLD and cooled:
                _last_alert[source] = now
                send = True
                lines = [d for (_, d) in recent[-10:] if d]
        if send:
            _email_founder(source, len(recent), lines)
    except Exception as e:
        print(f"[ops_alert] failed: {e}")


def _email_founder(source: str, count: int, lines: list[str]) -> None:
    try:
        from .channels import EmailChannel
        from .config import Settings
        to = os.environ.get("ADAPIX_FOUNDER_EMAIL", "roccochenet95@gmail.com")
        detail_block = "\n".join(f"  - {l}" for l in lines) or "  (no detail captured)"
        EmailChannel(Settings()).send(
            to,
            f"Adapix server alert: {count} errors in {WINDOW_MIN} min ({source})",
            (
                f"The Adapix server hit {count} errors from \"{source}\" within "
                f"{WINDOW_MIN} minutes.\n\nMost recent:\n{detail_block}\n\n"
                f"Check the Railway logs for the full picture. You'll get at most "
                f"one of these emails per {COOLDOWN_H} hours for this source."
            ),
            from_name="Adapix",
        )
        print(f"[ops_alert] founder email sent for source={source} count={count}")
    except Exception as e:
        print(f"[ops_alert] email failed: {e}")
