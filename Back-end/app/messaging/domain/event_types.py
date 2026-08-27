# ============================================================
# PaySentinelIQ — Event Types (Single Source of Truth)
# ============================================================
# Routing keys are ALWAYS derived from these constants.
# Never scatter arbitrary event strings across the codebase.
#
# Convention:  <domain>.<entity_or_context>.<verb>
#   e.g.  bank_slip.analysis.completed, bill.due_soon
# ============================================================

from __future__ import annotations

from enum import StrEnum


class EventType(StrEnum):
    """All domain event types published to the `sentinel.events` exchange."""

    # ── User lifecycle / auth ────────────────────────────────
    # payload.action carries the concrete verb: login | logout | register | mfa_verified
    USER_ACTION = "user.action"

    # ── Bank slip (boleto) analysis ──────────────────────────
    BANK_SLIP_ANALYSIS_STARTED = "bank_slip.analysis.started"
    BANK_SLIP_ANALYSIS_COMPLETED = "bank_slip.analysis.completed"
    BANK_SLIP_ANALYSIS_FAILED = "bank_slip.analysis.failed"

    # ── Payroll (contracheque) analysis ──────────────────────
    PAYROLL_ANALYSIS_STARTED = "payroll.analysis.started"
    PAYROLL_ANALYSIS_COMPLETED = "payroll.analysis.completed"
    PAYROLL_ANALYSIS_FAILED = "payroll.analysis.failed"

    # ── Generic document analysis (unknown document_type) ────
    DOCUMENT_ANALYSIS_STARTED = "document.analysis.started"
    DOCUMENT_ANALYSIS_COMPLETED = "document.analysis.completed"
    DOCUMENT_ANALYSIS_FAILED = "document.analysis.failed"

    # ── Bills / payment schedules ────────────────────────────
    BILL_SCHEDULED = "bill.scheduled"
    BILL_CANCELLED = "bill.cancelled"
    BILL_PAID = "bill.paid"
    BILL_DUE_SOON = "bill.due_soon"
    BILL_OVERDUE = "bill.overdue"

    # ── Notification center ──────────────────────────────────
    NOTIFICATION_CREATED = "notification.created"
    NOTIFICATION_READ = "notification.read"

    # ── Reports / activity views ─────────────────────────────
    REPORT_VIEWED = "report.viewed"


# ── Analysis event mapping ───────────────────────────────────
# Maps the uploaded document_type to the proper analysis event
# family. "boleto" → bank_slip.*, payroll-like → payroll.*,
# anything else → generic document.* events.

_BANK_SLIP_TYPES = frozenset({"boleto", "bank_slip", "bankslip"})
_PAYROLL_TYPES = frozenset({"contracheque", "holerite", "payroll"})


def analysis_event_types(document_type: str) -> tuple[EventType, EventType, EventType]:
    """Return (started, completed, failed) event types for a document_type."""
    normalized = (document_type or "").strip().lower()
    if normalized in _BANK_SLIP_TYPES:
        return (
            EventType.BANK_SLIP_ANALYSIS_STARTED,
            EventType.BANK_SLIP_ANALYSIS_COMPLETED,
            EventType.BANK_SLIP_ANALYSIS_FAILED,
        )
    if normalized in _PAYROLL_TYPES:
        return (
            EventType.PAYROLL_ANALYSIS_STARTED,
            EventType.PAYROLL_ANALYSIS_COMPLETED,
            EventType.PAYROLL_ANALYSIS_FAILED,
        )
    return (
        EventType.DOCUMENT_ANALYSIS_STARTED,
        EventType.DOCUMENT_ANALYSIS_COMPLETED,
        EventType.DOCUMENT_ANALYSIS_FAILED,
    )


# ── Audited events ───────────────────────────────────────────
# Which events the audit consumer persists to `audit_logs`.
# Deliberately excludes high-frequency / trivial traffic.

AUDITED_EVENT_TYPES: frozenset[str] = frozenset(et.value for et in EventType)
