"""Phone number normalization.

Everything that stores or looks up a phone number goes through
normalize_phone() so that "(412) 555-0100", "412-555-0100", "412.555.0100",
and "+14125550100" are all the SAME contact. Twilio delivers E.164; if our
stored numbers aren't E.164 too, inbound replies (including STOPs) silently
match nobody — the single most likely way to lose a customer's reply.

US/Canada-focused on purpose: that's who Adapix serves at launch. Numbers we
can't confidently normalize are returned stripped-but-untouched rather than
mangled.
"""
from __future__ import annotations

import re

_DIGITS = re.compile(r"\d+")


def normalize_phone(raw: str | None) -> str | None:
    """Best-effort E.164 (+1XXXXXXXXXX) for US/Canada numbers."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    digits = "".join(_DIGITS.findall(raw))
    if not digits:
        return None
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        # Already international format — trust it, just strip decoration.
        return "+" + digits
    # Can't confidently normalize (extensions, short codes, garbage):
    # keep the digits so at least formatting variants of the same bad
    # number dedupe against each other.
    return digits
