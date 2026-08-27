# ============================================================
# PaySentinelIQ — Email Service Implementations
# ============================================================
# - SMTPEmailService: real delivery via SMTP (blocking smtplib run
#   inside a worker thread — email NEVER blocks the event loop).
# - ConsoleEmailService: dev/test implementation that logs the email
#   instead of sending it (default when EMAIL_ENABLED=false).
# ============================================================

from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage as MIMEMessage

from app.notifications.domain.ports import EmailMessage, EmailService
from app.shared.settings import get_settings

logger = logging.getLogger(__name__)


class ConsoleEmailService:
    """Logs emails instead of sending them (local dev / CI)."""

    async def send(self, message: EmailMessage) -> None:
        logger.info(
            "email_console_send",
            extra={
                "to": message.to,
                "subject": message.subject,
                "body_length": len(message.body_text),
                "success": True,
            },
        )


class SMTPEmailService:
    """Delivers emails through an SMTP relay (STARTTLS supported)."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
        timeout: float = 15.0,
        from_address: str = "SentinelaPay <no-reply@paysentineliq.com>",
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._timeout = timeout
        self._from = from_address

    async def send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._send_blocking, message)

    def _send_blocking(self, message: EmailMessage) -> None:
        msg = MIMEMessage()
        msg["From"] = self._from
        msg["To"] = message.to
        msg["Subject"] = message.subject
        if message.reply_to:
            msg["Reply-To"] = message.reply_to
        msg.set_content(message.body_text or "")
        if message.body_html:
            msg.add_alternative(message.body_html, subtype="html")

        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as client:
            client.ehlo()
            if self._use_tls:
                client.starttls()
                client.ehlo()
            if self._username and self._password:
                client.login(self._username, self._password)
            client.send_message(msg)

        logger.info(
            "email_sent",
            extra={"to": message.to, "subject": message.subject, "success": True},
        )


def get_email_service() -> EmailService:
    """Build the configured email provider from application settings."""
    settings = get_settings()
    if not settings.EMAIL_ENABLED:
        return ConsoleEmailService()

    return SMTPEmailService(
        host=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USERNAME,
        password=settings.SMTP_PASSWORD.get_secret_value()
        if settings.SMTP_PASSWORD
        else None,
        use_tls=settings.SMTP_USE_TLS,
        timeout=settings.SMTP_TIMEOUT,
        from_address=settings.EMAIL_FROM,
    )
