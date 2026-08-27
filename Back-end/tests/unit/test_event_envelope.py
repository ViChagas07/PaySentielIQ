# ============================================================
# PaySentinelIQ — Event Envelope & Event Types Tests
# ============================================================

import uuid

from app.messaging.domain.envelope import (
    deterministic_event_id,
    new_event,
)
from app.messaging.domain.envelope import EventEnvelope
from app.messaging.domain.event_types import EventType, analysis_event_types


def test_envelope_serialization_roundtrip():
    event = new_event(
        EventType.BILL_DUE_SOON.value,
        payload={"bill_id": "abc", "amount": 129.9},
        user_id="00000000-0000-0000-0000-000000000001",
        tenant_id="00000000-0000-0000-0000-000000000002",
    )
    decoded = EventEnvelope.from_dict(event.to_dict())
    assert decoded.event_id == event.event_id
    assert decoded.event_type == EventType.BILL_DUE_SOON.value
    assert decoded.user_id == "00000000-0000-0000-0000-000000000001"
    assert decoded.payload["amount"] == 129.9
    assert decoded.version == 1


def test_envelope_defaults():
    event = new_event(EventType.REPORT_VIEWED.value)
    assert event.source == "paysentinel-api"
    assert event.version == 1
    uuid.UUID(event.event_id)  # valid UUID
    assert event.occurred_at.endswith("+00:00") or "T" in event.occurred_at


def test_envelope_from_dict_rejects_malformed():
    import pytest

    with pytest.raises(ValueError):
        EventEnvelope.from_dict({"event_type": "x"})
    with pytest.raises(ValueError):
        EventEnvelope.from_dict({"event_id": "1", "occurred_at": "now"})
    with pytest.raises(ValueError):
        EventEnvelope.from_dict("not-a-dict")


def test_payload_sanitization_strips_sensitive_keys():
    event = new_event(
        EventType.USER_ACTION.value,
        payload={"action": "login", "password": "secret", "token": "jwt"},
    )
    assert "password" not in event.payload
    assert "token" not in event.payload
    assert event.payload["action"] == "login"


def test_deterministic_event_id_is_stable():
    first = deterministic_event_id("bill", "id-1", "2026-08-27", "bill.due_soon")
    second = deterministic_event_id("bill", "id-1", "2026-08-27", "bill.due_soon")
    third = deterministic_event_id("bill", "id-1", "2026-08-28", "bill.due_soon")
    assert first == second
    assert first != third


def test_analysis_event_type_mapping():
    assert analysis_event_types("boleto") == (
        EventType.BANK_SLIP_ANALYSIS_STARTED,
        EventType.BANK_SLIP_ANALYSIS_COMPLETED,
        EventType.BANK_SLIP_ANALYSIS_FAILED,
    )
    assert analysis_event_types("contracheque")[0] == EventType.PAYROLL_ANALYSIS_STARTED
    assert analysis_event_types("holerite")[0] == EventType.PAYROLL_ANALYSIS_STARTED
    assert analysis_event_types("invoice")[0] == EventType.DOCUMENT_ANALYSIS_STARTED
