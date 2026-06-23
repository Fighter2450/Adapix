"""Channel adapters — SMS, email, voice (later).

Each channel implements `.send(...)` and returns a result with provider id +
status. All channels support a `dry_run` mode that prints to stdout instead
of actually sending — used for development and demos before BAAs are signed.
"""
from .sms import SmsChannel, SmsResult
from .email import EmailChannel, EmailResult

__all__ = ["SmsChannel", "SmsResult", "EmailChannel", "EmailResult"]
