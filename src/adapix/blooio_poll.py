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


def poll_blooio_inbound() -> int:
    """One pass: fetch every chat, process unseen inbound messages.
    Returns how many new inbound messages were processed."""
    settings = Settings()
    if not (settings.blooio_api_key and settings.prefer_imessage):
        return 0

    from .channels.imessage import get_contact, list_chat_messages, list_chats
    from .inbound import InboundProcessor
    from .phone import normalize_phone

    chats = list_chats(settings)
    if not chats:
        return 0

    processed = 0
    contact_cache: dict[str, str] = {}
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
        # chat-scoped listing, so resolve via the chat's contact identity.
        sender = None
        contact_id = chat.get("contact_id")
        if contact_id:
            if contact_id not in contact_cache:
                try:
                    contact = get_contact(settings, contact_id)
                    idents = (contact.get("identities") or [])
                    contact_cache[contact_id] = (idents[0].get("identifier") if idents else "") or ""
                except Exception:
                    contact_cache[contact_id] = ""
            sender = contact_cache[contact_id] or None
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
            if not mid or mid in seen or not text:
                continue
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
