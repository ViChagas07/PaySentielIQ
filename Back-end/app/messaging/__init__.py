# ============================================================
# PaySentinelIQ — Messaging Module (Event-Driven Architecture)
# ============================================================
# RabbitMQ-backed asynchronous eventing.
#
# Layers:
#   domain/         — event types, envelope, ports (no RabbitMQ imports)
#   application/    — handlers, retry policy, dedupe, schedulers
#   infrastructure/ — aio-pika RabbitMQ implementation + fakes
#
# The Domain/Application layers NEVER import aio-pika directly; they
# depend on the ports defined in `app.messaging.domain.ports`.
# ============================================================
