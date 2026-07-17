"""Blooio inbound POLLER — the reliability net under their webhooks.

Confirmed live on 7/16: an inbound message registered in Blooio's own API
(support's test reply) never triggered our registered `message.received`
webhook — zero delivery attempts, not even failed ones. Until their webhook
delivery is trustworthy, this poller pulls inbound messages straight from
their API every couple of minutes and feeds them into the exact same
pipeline the webhook uses (process_sms -> classification, STOP handling,
per-customer memory). Idempotent: a message id that already exists as a
Message.provider_id is skipped, so the webhook and the poller can never
double-process the same reply.
"""
from __future__ import annotations

from .config import Settings
from .db import get_session
from .models import Message, Organization

# Messages already handled THIS container-lifetime, whatever the outcome.
# The DB provider_id check only covers inbound that created a Message row;
# ignored ones (unknown sender) would otherwise be re-pulled every pass.
_seen_this_process: set[str] = set()


def poll_blooio_inbound() -> int:
    """One pass: fetch every chat, process unseen inbound messages.
    Returns how many new inbound messages were processed."""
    settings = Settings()
    if not (settings.blooio_api_key and settings.prefer_imessage):
        return 0

    from .channels.imessage import _blooio_get, list_chat_messages, list_chats
    from .inbound import InboundProcessor
    from .phone import normalize_phone

    chats = list_chats(settings)
    if not chats:
        return 0

    processed = 0
    # Contact identities come from the LIST endpoint — the per-contact
    # detail endpoint wraps/omits identities (verified live 7/16), which
    # silently starved the first version of this poller of every sender.
    contact_cache: dict[str, str] = {}
    try:
        for ct in _blooio_get(settings, "https://api.blooio.com/v4/contacts").get("data") or []:
            idents = ct.get("identities") or []
            if ct.get("id") and idents:
                contact_cache[ct["id"]] = idents[0].get("identifier") or ""
    except Exception:
        pass
    with get_session(settings) as s:
        org_by_channel = {
            o.blooio_channel_id: o for o in
            s.query(Organization).filter(Organization.blooio_channel_id.isnot(None)).all()
        }
        org_numbers = {o.id: o.imessage_number for o in org_by_channel.values()}

    for chat in chats:
        channel_id = chat.get("channel_id")
        org = org_by_channel.get(channel_id)
        if org is None:
            continue  # a chat on a channel no business has claimed

        msgs = [m for m in list_chat_messages(settings, chat["id"]) if m.get("direction") == "inbound"]
        if not msgs:
            continue

        # Who is this chat with? The message rows carry sender=None in the
        # chat-scoped listing, so resolve via the contact-list identities.
        sender = contact_cache.get(chat.get("contact_id") or "") or None
        if not sender:
            continue
        sender = normalize_phone(sender) or sender

        with get_session(settings) as s:
            seen = {
                pid for (pid,) in s.query(Message.provider_id)
                .filter(Message.provider_id.in_([m.get("id") for m in msgs if m.get("id")]))
                .all()
            }
        for m in msgs:
            mid = m.get("id")
            text = (m.get("text") or "").strip()
            if not mid or mid in seen or mid in _seen_this_process or not text:
                continue
            _seen_this_process.add(mid)
            try:
                result = InboundProcessor().process_sms(
                    from_number=sender, body=text,
                    provider_id=mid, to_number=org_numbers.get(org.id),
                )
                processed += 1
                print(f"[adapix] blooio poll: inbound from={sender} status={result.status}")
            except Exception as e:
                print(f"[adapix] blooio poll error on {mid}: {e}")
    return processed
