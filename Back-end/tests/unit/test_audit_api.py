# ============================================================
# PaySentinelIQ — Audit API / Activity History Tests
# ============================================================
# The Relatórios / Activity History API reads `audit_logs` (never
# RabbitMQ) with tenant scoping, filters and pagination.

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from app.audit.application.service import AuditLogFilter, AuditLogService
from app.shared.orm_models import AuditLogModel

TENANT = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()
USER_A = uuid.uuid4()
USER_B = uuid.uuid4()


async def _seed(session_factory) -> None:
    base = datetime.now(UTC) - timedelta(minutes=30)
    rows = [
        AuditLogModel(
            id=uuid.uuid4(), tenant_id=TENANT, user_id=USER_A, user_name="Ana",
            action="bank_slip.analysis.completed", entity_type="document",
            entity_id="d-1", details={}, created_at=None,
        ),
        AuditLogModel(
            id=uuid.uuid4(), tenant_id=TENANT, user_id=USER_A, user_name="Ana",
            action="bill.scheduled", entity_type="payment_schedule",
            entity_id="b-1", details={},
        ),
        AuditLogModel(
            id=uuid.uuid4(), tenant_id=TENANT, user_id=USER_B, user_name="Bruno",
            action="notification.read", entity_type="notification",
            entity_id="n-1", details={},
        ),
        # Other tenant — must never leak.
        AuditLogModel(
            id=uuid.uuid4(), tenant_id=OTHER_TENANT, user_id=USER_A, user_name="Ana",
            action="bill.scheduled", entity_type="payment_schedule",
            entity_id="b-9", details={},
        ),
    ]
    # created_at has a server default; set explicit timestamps so ordering
    # is deterministic in the test DB.
    for i, row in enumerate(rows):
        row.created_at = base + timedelta(minutes=i)
    async with session_factory() as session:
        # Isolate tests — the session-scoped SQLite engine is shared.
        await session.execute(delete(AuditLogModel))
        await session.commit()
        session.add_all(rows)
        await session.commit()


@pytest.mark.asyncio
async def test_lists_tenant_scoped_with_pagination(session_factory):
    await _seed(session_factory)
    async with session_factory() as session:
        service = AuditLogService(session)
        page = await service.list_for_tenant(
            TENANT, AuditLogFilter(page=1, page_size=2)
        )

    assert page.total == 3
    assert len(page.items) == 2
    assert page.total_pages == 2
    assert {row.action for row in page.items} >= {
        "notification.read",
        "bill.scheduled",
    }
    assert all(row.tenant_id == TENANT for row in page.items)


@pytest.mark.asyncio
async def test_filters_by_action_and_user(session_factory):
    await _seed(session_factory)
    async with session_factory() as session:
        service = AuditLogService(session)

        by_action = await service.list_for_tenant(
            TENANT, AuditLogFilter(action="bill.scheduled")
        )
        assert by_action.total == 1
        assert by_action.items[0].entity_id == "b-1"

        by_user = await service.list_for_tenant(
            TENANT, AuditLogFilter(user_id=USER_B)
        )
        assert by_user.total == 1
        assert by_user.items[0].action == "notification.read"


@pytest.mark.asyncio
async def test_action_icontains_filter(session_factory):
    await _seed(session_factory)
    async with session_factory() as session:
        service = AuditLogService(session)
        page = await service.list_for_tenant(
            TENANT, AuditLogFilter(action_icontains="analysis")
        )
    assert page.total == 1
    assert page.items[0].action == "bank_slip.analysis.completed"


@pytest.mark.asyncio
async def test_created_after_filter(session_factory):
    await _seed(session_factory)
    async with session_factory() as session:
        service = AuditLogService(session)
        page = await service.list_for_tenant(
            TENANT,
            AuditLogFilter(created_after=datetime.now(UTC) - timedelta(hours=1)),
        )
    assert page.total == 3


@pytest.mark.asyncio
async def test_my_activity_is_user_scoped(session_factory):
    await _seed(session_factory)
    async with session_factory() as session:
        service = AuditLogService(session)
        page = await service.list_for_user(TENANT, USER_A, AuditLogFilter())
    assert page.total == 2
    assert {row.action for row in page.items} == {
        "bank_slip.analysis.completed",
        "bill.scheduled",
    }


@pytest.mark.asyncio
async def test_response_shape_matches_frontend_contract(session_factory):
    await _seed(session_factory)
    async with session_factory() as session:
        service = AuditLogService(session)
        response = (await service.list_for_tenant(TENANT, AuditLogFilter())).to_response()

    assert set(response) == {"data", "total", "page", "page_size", "total_pages"}
    entry = response["data"][0]
    assert set(entry) == {
        "id", "tenant_id", "user_id", "user_name", "action", "entity_type",
        "entity_id", "details", "ip_address", "user_agent", "occurred_at",
        "created_at",
    }
