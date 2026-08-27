# ============================================================
# PaySentinelIQ — Event Envelope
# ============================================================
# Canonical message contract for every event published to the
# broker. Payloads carry ONLY identifiers + small metadata —
# never documents, files, credentials, tokens or passwords.
# ============================================================

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

ENVELOPE_VERSION = 1

# Keys that must never appear inside a payload (defense in depth).
_SENSITIVE_KEYS = frozenset(
    {"password", "hashed_password", "token", "access_token", "refresh_token",
     "secret", "mfa_secret", "api_key", "file_bytes", "pdf", "file_data"}
)


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop obviously sensitive keys from a payload before publishing."""
    return {k: v for k, v in payload.items() if k.lower() not in _SENSITIVE_KEYS}


@dataclass(frozen=True)
class EventEnvelope:
    """Immutable event contract exchanged through the message broker."""

    event_id: str
    event_type: str
    occurred_at: str  # ISO-8601 (UTC)
    source: str
    version: int
    user_id: str | None = None
    tenant_id: str | None = None
    correlation_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Serialization ────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "source": self.source,
            "version": self.version,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "correlation_id": self.correlation_id,
            "payload": self.payload,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventEnvelope:
        """Rebuild an envelope from a decoded message body.

        Raises:
            ValueError: if mandatory fields are missing/invalid.
        """
        if not isinstance(data, dict):
            raise ValueError("Event body must be a JSON object")

        event_id = data.get("event_id")
        event_type = data.get("event_type")
        occurred_at = data.get("occurred_at")
        if not event_id or not isinstance(event_id, str):
            raise ValueError("Missing or invalid 'event_id'")
        if not event_type or not isinstance(event_type, str):
            raise ValueError("Missing or invalid 'event_type'")
        if not occurred_at or not isinstance(occurred_at, str):
            raise ValueError("Missing or invalid 'occurred_at'")

        payload = data.get("payload") or {}
        metadata = data.get("metadata") or {}
        if not isinstance(payload, dict) or not isinstance(metadata, dict):
            raise ValueError("'payload' and 'metadata' must be objects")

        return cls(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            source=str(data.get("source") or "unknown"),
            version=int(data.get("version") or ENVELOPE_VERSION),
            user_id=data.get("user_id"),
            tenant_id=data.get("tenant_id"),
            correlation_id=data.get("correlation_id"),
            payload=payload,
            metadata=metadata,
        )


def new_event(
    event_type: str,
    *,
    payload: dict[str, Any] | None = None,
    user_id: str | None = None,
    tenant_id: str | None = None,
    correlation_id: str | None = None,
    event_id: str | None = None,
    source: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> EventEnvelope:
    """Factory that builds a consistent envelope for a new domain event.

    - ``event_id``: pass a deterministic UUID (e.g. uuid5) for naturally
      idempotent events such as scheduled reminders; a random UUID is
      generated otherwise.
    - ``correlation_id``: when omitted, the current CorrelationContext
      request/trace id is used so HTTP-originated events stay traceable.
    """
    from app.shared.settings import get_settings

    if correlation_id is None:
        try:
            from app.observability.correlation import get_correlation

            ctx = get_correlation()
            correlation_id = ctx.request_id or ctx.trace_id or None
        except Exception:  # pragma: no cover - correlation must never break publishing
            correlation_id = None

    return EventEnvelope(
        event_id=event_id or str(uuid.uuid4()),
        event_type=str(event_type),
        occurred_at=datetime.now(UTC).isoformat(),
        source=source or get_settings().EVENT_SOURCE,
        version=ENVELOPE_VERSION,
        user_id=user_id,
        tenant_id=tenant_id,
        correlation_id=correlation_id,
        payload=_sanitize_payload(payload or {}),
        metadata=metadata or {},
    )


def deterministic_event_id(*parts: object) -> str:
    """Build a deterministic UUIDv5 for naturally idempotent events.

    Consumers dedupe by event_id, so republishing the same logical
    occurrence (e.g. the same bill on the same day) is harmless.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, ":".join(str(p) for p in parts)))
