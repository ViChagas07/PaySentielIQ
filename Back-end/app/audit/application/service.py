# ============================================================
# PaySentinelIQ — Audit Log Service (Read Model)
# ============================================================
# Serves the Relatórios / Activity History feed from the immutable
# `audit_logs` table. Never queries RabbitMQ — the broker is only a
# transport; the database is the source of truth for history.
# ============================================================

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.orm_models import AuditLogModel


@dataclass(frozen=True)
class AuditLogFilter:
    """Query filters for the activity history feed."""

    user_id: uuid.UUID | None = None
    action: str | None = None
    entity_type: str | None = None
    action_icontains: str | None = None  # substring match (frontend "AI only")
    created_after: datetime | None = None
    created_before: datetime | None = None
    page: int = 1
    page_size: int = 50


@dataclass(frozen=True)
class AuditLogPage:
    """One page of the activity history feed."""

    items: list[AuditLogModel]
    total: int
    page: int
    page_size: int

    @property
    def total_pages(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)

    def to_response(self) -> dict[str, Any]:
        return {
            "data": [self._serialize(row) for row in self.items],
            "total": self.total,
            "page": self.page,
            "page_size": self.page_size,
            "total_pages": self.total_pages,
        }

    @staticmethod
    def _serialize(row: AuditLogModel) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "tenant_id": str(row.tenant_id),
            "user_id": str(row.user_id) if row.user_id else None,
            "user_name": row.user_name,
            "action": row.action,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "details": row.details or {},
            "ip_address": row.ip_address,
            "user_agent": row.user_agent,
            "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


class AuditLogService:
    """Read use cases over the append-only audit log."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_tenant(
        self, tenant_id: uuid.UUID, filters: AuditLogFilter
    ) -> AuditLogPage:
        """List activity history for a tenant (auditor view)."""
        base = AuditLogModel.tenant_id == tenant_id
        return await self._query(base, filters)

    async def list_for_user(
        self, tenant_id: uuid.UUID, user_id: uuid.UUID, filters: AuditLogFilter
    ) -> AuditLogPage:
        """List the activity history of a single user (self-service view)."""
        base = (AuditLogModel.tenant_id == tenant_id) & (
            AuditLogModel.user_id == user_id
        )
        return await self._query(base, filters)

    async def _query(self, base: Any, filters: AuditLogFilter) -> AuditLogPage:
        conditions = [base]

        if filters.user_id is not None:
            conditions.append(AuditLogModel.user_id == filters.user_id)
        if filters.action:
            conditions.append(AuditLogModel.action == filters.action)
        if filters.entity_type:
            conditions.append(AuditLogModel.entity_type == filters.entity_type)
        if filters.action_icontains:
            conditions.append(AuditLogModel.action.ilike(f"%{filters.action_icontains}%"))
        if filters.created_after is not None:
            conditions.append(AuditLogModel.created_at >= filters.created_after)
        if filters.created_before is not None:
            conditions.append(AuditLogModel.created_at <= filters.created_before)

        # Combine every condition with AND.
        where = conditions[0]
        for extra in conditions[1:]:
            where = where & extra

        count_stmt = select(func.count(AuditLogModel.id)).where(where)
        total = (await self.session.execute(count_stmt)).scalar_one()

        page = max(1, filters.page)
        size = min(max(1, filters.page_size), 200)
        stmt = (
            select(AuditLogModel)
            .where(where)
            .order_by(AuditLogModel.created_at.desc(), AuditLogModel.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())

        return AuditLogPage(items=rows, total=int(total), page=page, page_size=size)
