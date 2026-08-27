# ============================================================
# PaySentinelIQ — Notifications Domain (ports)
# ============================================================
# Email is one delivery channel of the existing Notification Center.
# The EmailService port keeps consumers decoupled from any concrete
# provider (SMTP today; SES/SendGrid later without touching workers).
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EmailMessage:
    """Provider-agnostic transactional email."""

    to: str
    subject: str
    body_text: str
    body_html: str | None = None
    reply_to: str | None = None


@runtime_checkable
class EmailService(Protocol):
    """Delivers transactional emails through a concrete provider."""

    async def send(self, message: EmailMessage) -> None:
        """Send one email. Raises on transient provider failures."""
        ...
