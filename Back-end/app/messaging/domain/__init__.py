# ============================================================
# PaySentinelIQ — Messaging Domain
# Pure domain objects: no aio-pika / infrastructure imports here.
# ============================================================

from app.messaging.domain.envelope import EventEnvelope, new_event
from app.messaging.domain.event_types import EventType, analysis_event_types
from app.messaging.domain.ports import (
    EventConsumer,
    EventHandler,
    EventPublisher,
    PermanentProcessingError,
    TransientProcessingError,
)

__all__ = [
    "EventConsumer",
    "EventEnvelope",
    "EventHandler",
    "EventPublisher",
    "EventType",
    "PermanentProcessingError",
    "TransientProcessingError",
    "analysis_event_types",
    "new_event",
]
