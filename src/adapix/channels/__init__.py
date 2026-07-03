"""Channel adapters — SMS, email, voice.

Each channel returns a result with a provider id + status. All channels support
a `dry_run` mode that prints instead of actually sending/calling — used for
development and demos before real carrier / Vapi accounts are wired in.

  SmsChannel.send(to, body)                     -> SmsResult
  EmailChannel.send(to, subject, body)          -> EmailResult
  VoiceChannel.place_call(to, system_prompt, …) -> VoiceResult
  IMessageChannel.send(to, body)                -> IMessageResult
"""
from .sms import SmsChannel, SmsResult
from .email import EmailChannel, EmailResult
from .voice import VoiceChannel, VoiceResult, ai_disclosure_line
from .imessage import IMessageChannel, IMessageResult

__all__ = [
    "SmsChannel", "SmsResult",
    "EmailChannel", "EmailResult",
    "VoiceChannel", "VoiceResult", "ai_disclosure_line",
    "IMessageChannel", "IMessageResult",
]
