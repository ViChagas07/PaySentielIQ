# ============================================================
# PaySentinelIQ — Messaging & Activity History
# Revision: d4e5f6a7b8c9
#
# 1. processed_events — consumer idempotency (inbox pattern)
# 2. audit_logs.event_id — deterministic dedupe of domain events
# 3. audit_logs.occurred_at — when the event happened (vs. created_at,
#    which is when the row was persisted by the consumer)
# ============================================================

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── processed_events (consumer inbox / idempotency) ──────
    op.create_table(
        "processed_events",
        sa.Column(
            "id",
            postgresql.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("consumer", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(150), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("event_id", "consumer", name="uq_processed_event_consumer"),
    )
    op.create_index(
        "ix_processed_events_consumer", "processed_events", ["consumer"]
    )

    # ── audit_logs: event-driven columns ─────────────────────
    op.add_column("audit_logs", sa.Column("event_id", sa.String(64), nullable=True))
    op.add_column(
        "audit_logs", sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint("uq_audit_logs_event_id", "audit_logs", ["event_id"])
    op.create_index("ix_audit_logs_event_id", "audit_logs", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_event_id", table_name="audit_logs")
    op.drop_constraint("uq_audit_logs_event_id", "audit_logs", type_="unique")
    op.drop_column("audit_logs", "occurred_at")
    op.drop_column("audit_logs", "event_id")

    op.drop_index("ix_processed_events_consumer", table_name="processed_events")
    op.drop_table("processed_events")
