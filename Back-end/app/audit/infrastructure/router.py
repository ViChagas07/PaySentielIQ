# ============================================================
# PaySentinelIQ — Audit Logs Router (Activity History / Relatórios)
# ============================================================
# Read-only feed backed by the immutable `audit_logs` table, which is
# fed asynchronously by the `sentinel.audit` RabbitMQ consumer.
# The frontend NEVER queries RabbitMQ — it reads this API.
#
# Endpoints:
#   GET /api/audit-logs     — tenant-wide timeline (auditor roles)
#   GET /api/audit-logs/me  — current user's own activity (any role)
# ============================================================

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.application.service import AuditLogFilter, AuditLogService
from app.auth.dependencies import get_current_tenant_id, get_current_user_id, require_auditor
from app.shared.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()

_ACTIONS = frozenset(
    {
        "user.action",
        "bank_slip.analysis.started",
        "bank_slip.analysis.completed",
        "bank_slip.analysis.failed",
        "payroll.analysis.started",
        "payroll.analysis.completed",
        "payroll.analysis.failed",
        "document.analysis.started",
        "document.analysis.completed",
        "document.analysis.failed",
        "bill.scheduled",
        "bill.cancelled",
        "bill.paid",
        "bill.due_soon",
        "bill.overdue",
        "notification.created",
        "notification.read",
        "report.viewed",
    }
)


def _parse_filter(
    action: str | None,
    user_id: str | None,
    entity_type: str | None,
    action_icontains: str | None,
    created_after: str | None,
    created_before: str | None,
    page: int,
    page_size: int,
) -> AuditLogFilter:
    """Translate raw query params into a validated AuditLogFilter."""

    def _dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    parsed_user_id: uuid.UUID | None = None
    if user_id:
        try:
            parsed_user_id = uuid.UUID(user_id)
        except ValueError:
            parsed_user_id = None

    return AuditLogFilter(
        user_id=parsed_user_id,
        action=action or None,
        entity_type=entity_type or None,
        action_icontains=action_icontains or None,
        created_after=_dt(created_after),
        created_before=_dt(created_before),
        page=page,
        page_size=page_size,
    )


async def _publish_report_view(
    tenant_id: str, user_id: str, payload: dict[str, Any]
) -> None:
    """Emit `report.viewed` for the activity-history view (page 1 only).

    Deliberately NOT published on pagination/polling requests to avoid
    flooding the audit trail with trivial reads.
    """
    try:
        from app.messaging.domain.envelope import new_event
        from app.messaging.domain.event_types import EventType
        from app.messaging.infrastructure.factory import get_event_publisher

        event = new_event(
            EventType.REPORT_VIEWED.value,
            payload=payload,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        await get_event_publisher().publish(event)
    except Exception as exc:  # pragma: no cover - audit of audit must never fail
        logger.warning("report.viewed publish failed (non-fatal): %s", exc)


@router.get("")
async def list_audit_logs(
    tenant_id: str = Depends(get_current_tenant_id),
    payload: dict[str, Any] = Depends(require_auditor),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    action: str | None = None,
    user_id: str | None = None,
    entity_type: str | None = None,
    action_icontains: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    from_date: str | None = None,  # legacy alias of created_after
    to_date: str | None = None,  # legacy alias of created_before
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List immutable activity history for the tenant (paginated).

    Filters: action, user_id, entity_type, action__icontains,
    created_after/created_before (ISO-8601). Read-only by design.
    """
    filters = _parse_filter(
        action=action,
        user_id=user_id,
        entity_type=entity_type,
        action_icontains=action_icontains,
        created_after=created_after or from_date,
        created_before=created_before or to_date,
        page=page,
        page_size=page_size,
    )
    service = AuditLogService(db)
    result = await service.list_for_tenant(uuid.UUID(tenant_id), filters)

    # Auditing the audit view itself (page 1 only — avoids polling noise).
    if page == 1:
        await _publish_report_view(
            tenant_id,
            str(payload["sub"]),
            {
                "report": "activity_history",
                "filters": {
                    k: v
                    for k, v in {
                        "action": action,
                        "entity_type": entity_type,
                        "created_after": created_after or from_date,
                    }.items()
                    if v
                },
            },
        )
    return result.to_response()


@router.get("/me")
async def list_my_audit_logs(
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    action: str | None = None,
    entity_type: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List the authenticated user's own activity (self-service, any role)."""
    filters = _parse_filter(
        action=action,
        user_id=None,
        entity_type=entity_type,
        action_icontains=None,
        created_after=created_after,
        created_before=created_before,
        page=page,
        page_size=page_size,
    )
    service = AuditLogService(db)
    result = await service.list_for_user(
        uuid.UUID(tenant_id), uuid.UUID(user_id), filters
    )
    return result.to_response()
