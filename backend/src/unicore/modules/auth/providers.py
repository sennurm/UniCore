"""Pluggable OTP/credential delivery (locked 25-07-2026): SmsProvider interface,
dev stub + email fallback now; the DLT-registered provider wires in when its
credentials arrive. The stub records messages so tests and local dev can read
what would have been sent — codes are never logged."""

from dataclasses import dataclass
from typing import Protocol

from unicore.core.logging import get_logger, timed


@dataclass(frozen=True)
class Message:
    channel: str  # 'sms' | 'email'
    to: str
    body: str


class SmsProvider(Protocol):
    async def send(self, to: str, body: str) -> None: ...


class DevStubProvider:
    """Records instead of sending. Replaced per-environment via settings later."""

    def __init__(self, channel: str) -> None:
        self.channel = channel
        self.outbox: list[Message] = []

    async def send(self, to: str, body: str) -> None:
        self.outbox.append(Message(self.channel, to, body))


sms_provider = DevStubProvider("sms")
email_provider = DevStubProvider("email")


async def deliver(mobile: str | None, email: str | None, body: str) -> str:
    """SMS primary, email fallback (AUTH-FR-02). Returns the channel used."""
    if mobile:
        with timed("sms send completed", **{"messaging.system": "sms-stub"}):
            await sms_provider.send(mobile, body)
        return "sms"
    if email:
        with timed("email send completed", **{"messaging.system": "email-stub"}):
            await email_provider.send(email, body)
        return "email"
    get_logger().error("no delivery channel for user message")
    raise ValueError("User has no mobile or email on record.")
