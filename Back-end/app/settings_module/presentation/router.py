# ============================================================
# PaySentinelIQ — User Settings / Preferences Router
# Persists user preferences (theme, notifications, accessibility)
# ============================================================


from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_tenant_id, get_current_user_id
from app.shared.database import get_db

router = APIRouter()


class UserSettingsRequest(BaseModel):
    """User preferences schema matching front-end SettingsState."""

    model_config = ConfigDict(strict=True)

    # Appearance
    theme: str | None = Field(default=None, pattern="^(dark|light|system)$")
    primary_color: str | None = Field(default=None, pattern="^(blue|green|purple|orange|red|teal)$")
    background_color: str | None = Field(
        default=None, pattern="^(navy|charcoal|slate|midnight|espresso|forest)$"
    )
    bold_text: bool | None = None
    font_size: str | None = Field(default=None, pattern="^(small|medium|large|xlarge)$")
    element_size: str | None = Field(default=None, pattern="^(compact|comfortable|spacious)$")

    # Language
    locale: str | None = None

    # Accessibility
    high_contrast: bool | None = None
    screen_reader_optimized: bool | None = None
    keyboard_nav: bool | None = None
    focus_indicator: bool | None = None
    dyslexia_font: bool | None = None
    reduced_motion: bool | None = None

    # Notifications
    email_alerts: bool | None = None
    push_notifications: bool | None = None
    desktop_alerts: bool | None = None
    sound_alerts: bool | None = None
    whatsapp_alerts: bool | None = None
    telegram_alerts: bool | None = None
    slack_alerts: bool | None = None
    in_app_alerts: bool | None = None
    alert_threshold: int | None = Field(default=None, ge=0, le=100)
    fraud_alert_email: str | None = None
    digest_frequency: str | None = Field(default=None, pattern="^(daily|weekly|monthly|never)$")

    # Notification destinations
    telegram_username: str | None = Field(default=None, max_length=64)
    whatsapp_number: str | None = Field(default=None, max_length=32)
    slack_destination: str | None = Field(default=None, max_length=128)

    # Account
    timezone: str | None = None

    # Developer
    developer_mode: bool | None = None


class UserSettingsResponse(BaseModel):
    """Full user settings response."""

    model_config = ConfigDict(strict=True)

    # Appearance
    theme: str = "dark"
    primary_color: str = "blue"
    background_color: str = "navy"
    bold_text: bool = False
    font_size: str = "medium"
    element_size: str = "comfortable"

    # Language
    locale: str = "en"

    # Accessibility
    high_contrast: bool = False
    screen_reader_optimized: bool = False
    keyboard_nav: bool = True
    focus_indicator: bool = True
    dyslexia_font: bool = False
    reduced_motion: bool = False

    # Notifications
    email_alerts: bool = True
    push_notifications: bool = False
    desktop_alerts: bool = False
    sound_alerts: bool = False
    whatsapp_alerts: bool = False
    telegram_alerts: bool = False
    slack_alerts: bool = False
    in_app_alerts: bool = False
    alert_threshold: int = 70
    fraud_alert_email: str = ""
    digest_frequency: str = "daily"

    # Notification destinations
    telegram_username: str | None = None
    whatsapp_number: str | None = None
    slack_destination: str | None = None

    # Account
    timezone: str = "America/Sao_Paulo"

    # Developer
    developer_mode: bool = False


# ── In-memory settings store (production: PostgreSQL or Redis) ──
_user_settings: dict[str, dict[str, Any]] = {}


def _get_defaults() -> dict[str, Any]:
    return UserSettingsResponse().model_dump()


def _get_or_create_settings(user_id: str) -> dict[str, Any]:
    if user_id not in _user_settings:
        _user_settings[user_id] = _get_defaults()
    return _user_settings[user_id]


@router.get("", response_model=UserSettingsResponse)
async def get_settings(
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
) -> UserSettingsResponse:
    """Get current user preferences/settings."""
    settings = _get_or_create_settings(user_id)
    return UserSettingsResponse(**settings)


@router.put("", response_model=UserSettingsResponse)
async def update_settings(
    body: UserSettingsRequest,
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
) -> UserSettingsResponse:
    """Update user preferences. Only provided fields are changed."""
    current = _get_or_create_settings(user_id)

    # Merge only provided (non-None) fields
    update_data = body.model_dump(exclude_none=True)
    current.update(update_data)

    _user_settings[user_id] = current

    return UserSettingsResponse(**current)


@router.post("/reset", response_model=UserSettingsResponse)
async def reset_settings(
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
) -> UserSettingsResponse:
    """Reset all user preferences to defaults."""
    defaults = _get_defaults()
    _user_settings[user_id] = defaults
    return UserSettingsResponse(**defaults)


# ══════════════════════════════════════════════════════
# PAYMENT REMINDER PREFERENCES
# ══════════════════════════════════════════════════════


class ReminderPreferencesRequest(BaseModel):
    model_config = ConfigDict(strict=False)
    every_2_days: bool = False
    weekly: bool = False
    monthly: bool = False
    on_due_date: bool = True


@router.get("/reminders", response_model=ReminderPreferencesRequest)
async def get_reminder_preferences(
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> ReminderPreferencesRequest:
    """Get the current user's payment reminder frequency preferences."""
    try:
        from app.shared.orm_models import UserSettingsModel

        result = await db.execute(
            select(UserSettingsModel).where(UserSettingsModel.user_id == user_id)
        )
        settings = result.scalar_one_or_none()

        if settings and settings.reminder_preferences:
            return ReminderPreferencesRequest(**settings.reminder_preferences)
        return ReminderPreferencesRequest()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/reminders", response_model=ReminderPreferencesRequest)
async def update_reminder_preferences(
    body: ReminderPreferencesRequest,
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> ReminderPreferencesRequest:
    """Update the user's payment reminder frequency preferences."""
    try:
        from app.shared.orm_models import UserSettingsModel

        result = await db.execute(
            select(UserSettingsModel).where(UserSettingsModel.user_id == user_id)
        )
        settings = result.scalar_one_or_none()

        if not settings:
            raise HTTPException(status_code=404, detail="User settings not found")

        settings.reminder_preferences = body.model_dump()
        db.add(settings)
        await db.commit()
        return body
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e)) from e


# ══════════════════════════════════════════════════════
# PAYMENT SCHEDULES (Boleto Tracking)
# ══════════════════════════════════════════════════════


class PaymentScheduleResponse(BaseModel):
    model_config = ConfigDict(strict=False)
    id: str
    due_date: str
    amount: float
    beneficiary: str
    bank_code: str
    status: str
    boleto_data: dict[str, Any] = {}
    created_at: str


@router.get("/payment-schedules")
async def list_payment_schedules(
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[PaymentScheduleResponse]:
    """List payment schedules (boleto due dates) for the current user."""
    try:
        from app.shared.orm_models import PaymentScheduleModel

        query = select(PaymentScheduleModel).where(
            PaymentScheduleModel.user_id == user_id
        )
        if status:
            query = query.where(PaymentScheduleModel.status == status)
        query = query.order_by(desc(PaymentScheduleModel.due_date))

        result = await db.execute(query)
        schedules = list(result.scalars().all())

        return [
            PaymentScheduleResponse(
                id=str(s.id),
                due_date=s.due_date.isoformat(),
                amount=s.amount,
                beneficiary=s.beneficiary,
                bank_code=s.bank_code,
                status=s.status,
                boleto_data=s.boleto_data or {},
                created_at=s.created_at.isoformat(),
            )
            for s in schedules
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.patch("/payment-schedules/{schedule_id}/status")
async def update_payment_status(
    schedule_id: str,
    status: str = Query(..., pattern="^(paid|cancelled)$"),
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Mark a payment schedule as paid or cancelled."""
    try:
        from app.shared.orm_models import PaymentScheduleModel

        result = await db.execute(
            select(PaymentScheduleModel).where(
                and_(
                    PaymentScheduleModel.id == schedule_id,
                    PaymentScheduleModel.user_id == user_id,
                )
            )
        )
        schedule = result.scalar_one_or_none()

        if not schedule:
            raise HTTPException(status_code=404, detail="Payment schedule not found")

        schedule.status = status
        db.add(schedule)
        await db.commit()

        # ── Publish bill.cancelled / bill.paid (activity history) ──
        event_type = "bill.cancelled" if status == "cancelled" else "bill.paid"
        await _publish_bill_event(
            event_type,
            schedule_id=schedule_id,
            user_id=user_id,
            tenant_id=tenant_id,
            payload={
                "bill_id": schedule_id,
                "due_date": schedule.due_date.isoformat(),
                "amount": schedule.amount,
                "beneficiary": schedule.beneficiary,
            },
        )

        return {"status": "updated", "schedule_id": schedule_id, "new_status": status}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e)) from e


# ══════════════════════════════════════════════════════
# PAYMENT SCHEDULE REGISTRATION (with validation)
# ══════════════════════════════════════════════════════


class RegisterPaymentRequest(BaseModel):
    model_config = ConfigDict(strict=False)
    due_date: str  # ISO format
    amount: float
    beneficiary: str = ""
    bank_code: str = ""
    boleto_data: dict[str, Any] = {}


@router.post("/payment-schedules")
async def register_payment_schedule(
    body: RegisterPaymentRequest,
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Register a new payment schedule (boleto) for tracking.
    Validates:
      - Due date is not in the past
      - No duplicate (same amount + beneficiary + due date already paid)
    """
    try:
        import uuid as _uuid
        from datetime import datetime as _dt, timezone as _tz

        from app.shared.orm_models import PaymentScheduleModel

        # Parse due date
        due_date = _dt.fromisoformat(body.due_date)
        now = _dt.now(_tz.utc)

        # ── Validation 1: Check if overdue ──
        if due_date.replace(tzinfo=_tz.utc) < now:
            return {
                "status": "rejected",
                "reason": "overdue",
                "message": "Pagamento vencido! Selecione um cujo prazo esteja em andamento.",
                "due_date": body.due_date,
            }

        # ── Validation 2: Check if already paid (duplicate) ──
        existing_result = await db.execute(
            select(PaymentScheduleModel).where(
                and_(
                    PaymentScheduleModel.user_id == user_id,
                    PaymentScheduleModel.beneficiary == body.beneficiary,
                    PaymentScheduleModel.amount == body.amount,
                    PaymentScheduleModel.status == "paid",
                )
            )
        )
        already_paid = existing_result.scalar_one_or_none()

        if already_paid:
            return {
                "status": "rejected",
                "reason": "already_paid",
                "message": "Pagamento já foi pago.",
                "existing_schedule_id": str(already_paid.id),
            }

        # ── Create schedule ──
        schedule = PaymentScheduleModel(
            id=_uuid.uuid4(),
            user_id=_uuid.UUID(user_id),
            tenant_id=_uuid.UUID(tenant_id),
            due_date=due_date,
            amount=body.amount,
            beneficiary=body.beneficiary,
            bank_code=body.bank_code,
            boleto_data=body.boleto_data,
            status="pending",
        )
        db.add(schedule)
        await db.commit()

        # ── Publish bill.scheduled (activity history) ──
        await _publish_bill_event(
            "bill.scheduled",
            schedule_id=str(schedule.id),
            user_id=user_id,
            tenant_id=tenant_id,
            payload={
                "bill_id": str(schedule.id),
                "due_date": body.due_date,
                "amount": body.amount,
                "beneficiary": body.beneficiary,
                "bank_code": body.bank_code,
            },
        )

        return {
            "status": "registered",
            "schedule_id": str(schedule.id),
            "due_date": body.due_date,
            "amount": body.amount,
            "beneficiary": body.beneficiary,
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _publish_bill_event(
    event_type: str,
    *,
    schedule_id: str,
    user_id: str,
    tenant_id: str,
    payload: dict[str, Any],
) -> None:
    """Publish a bill lifecycle event to the activity history (non-fatal)."""
    try:
        from app.messaging.domain.envelope import new_event
        from app.messaging.infrastructure.factory import get_event_publisher

        event = new_event(
            event_type,
            payload={"bill_id": schedule_id, **payload},
            user_id=user_id,
            tenant_id=tenant_id,
        )
        await get_event_publisher().publish(event)
    except Exception as exc:  # pragma: no cover - events must never fail the API
        import logging

        logging.getLogger(__name__).warning(
            "bill event publish failed (non-fatal): %s", exc
        )
