# ============================================================
# PaySentinelIQ — Consumer Handlers
# ============================================================
# Each handler is the business logic attached to a RabbitMQ queue.
# They depend only on the DB session factory and the domain ports —
# nothing here knows about aio-pika or queue topology.
# ============================================================

from app.messaging.application.handlers.audit import AuditEventHandler
from app.messaging.application.handlers.email import EmailEventHandler
from app.messaging.application.handlers.notifications import NotificationEventHandler

__all__ = ["AuditEventHandler", "EmailEventHandler", "NotificationEventHandler"]
