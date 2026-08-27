# ============================================================
# PaySentinelIQ — Notifications Router
# Production endpoints backed by NotificationService + DB.
# Returns real data only — no mock fallbacks.
# ============================================================

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_tenant_id, get_current_user_id
from app.shared.database import get_db
from app.notifications.services import NotificationService

router = APIRouter()


def _service(db: AsyncSession) -> NotificationService:
    """Build the NotificationService with the process-wide event publisher."""
    from app.messaging.infrastructure.factory import get_event_publisher

    return NotificationService(db, event_publisher=get_event_publisher())


async def _publish_notification_read(
    user_id: str,
    tenant_id: str,
    *,
    notification_ids: list[str],
) -> None:
    """Publish notification.read for the activity history (non-fatal)."""
    try:
        from app.messaging.domain.envelope import new_event
        from app.messaging.domain.event_types import EventType
        from app.messaging.infrastructure.factory import get_event_publisher

        event = new_event(
            EventType.NOTIFICATION_READ.value,
            payload={"notification_ids": notification_ids[:50]},
            user_id=user_id,
            tenant_id=tenant_id,
        )
        await get_event_publisher().publish(event)
    except Exception:  # pragma: no cover - audit must never fail the endpoint
        import logging

        logging.getLogger(__name__).warning(
            "notification.read publish failed (non-fatal)", exc_info=True
        )


# ── Response Models ──


class NotificationResponse(BaseModel):
    model_config = ConfigDict(strict=False)
    id: str
    user_id: str | None = None
    tenant_id: str | None = None
    type: str
    title: str
    message: str
    severity: str = "normal"
    is_read: bool = False
    action_url: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str


class NotificationSettingsRequest(BaseModel):
    model_config = ConfigDict(strict=False)
    email: bool | None = None
    whatsapp: bool | None = None
    telegram: bool | None = None
    slack: bool | None = None
    inApp: bool | None = None


# ── Helper: convert ORM model to response dict ──


def _notification_to_response(notification: Any) -> dict[str, Any]:
    """Convert NotificationModel to API response format."""
    return {
        "id": str(notification.id),
        "user_id": str(notification.user_id),
        "tenant_id": str(notification.tenant_id),
        "type": notification.type,
        "title": notification.title,
        "message": notification.message,
        "severity": getattr(notification, "severity", "normal"),
        "is_read": notification.is_read,
        "action_url": notification.action_url,
        "metadata": getattr(notification, "metadata_", None),
        "created_at": notification.created_at.isoformat() if hasattr(notification.created_at, "isoformat") else notification.created_at,
    }


# ══════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════


@router.get("")
async def list_notifications(
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    unread_only: bool = False,
    severity: str | None = None,
    type: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    List notifications for the current user with optional filtering and pagination.
    Returns real data from the database — no mock fallbacks.
    """
    try:
        service = _service(db)
        notifications = await service.get_notifications_for_user(
            user_id=uuid.UUID(user_id),
            skip=(page - 1) * page_size,
            limit=page_size,
            is_read=False if unread_only else None,
            severity=severity,
            type=type,
        )

        total = len(notifications)
        return {
            "data": [_notification_to_response(n) for n in notifications],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total + page_size - 1) // page_size),
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch notifications: {str(e)}") from e


@router.get("/unread-count")
async def get_unread_count(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get count of unread notifications for the current user."""
    try:
        service = NotificationService(db)
        count = await service.get_unread_notification_count(user_id=uuid.UUID(user_id))
        return {"count": count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch unread count: {str(e)}") from e


@router.patch("/{notification_id}/read")
async def mark_as_read(
    notification_id: str,
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Mark a single notification as read."""
    try:
        service = _service(db)
        notification = await service.mark_notification_as_read(
            notification_id=uuid.UUID(notification_id),
            user_id=uuid.UUID(user_id),
        )
        await db.commit()
        await _publish_notification_read(
            user_id, tenant_id, notification_ids=[notification_id]
        )
        return {
            "status": "read",
            "notification_id": str(notification.id),
            "notification": _notification_to_response(notification),
        }
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/{notification_id}/unread")
async def mark_as_unread(
    notification_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Mark a single notification as unread."""
    try:
        service = NotificationService(db)
        notification = await service.mark_notification_as_unread(
            notification_id=uuid.UUID(notification_id),
            user_id=uuid.UUID(user_id),
        )
        await db.commit()
        return {
            "status": "unread",
            "notification_id": str(notification.id),
            "notification": _notification_to_response(notification),
        }
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/read-all")
async def mark_all_read(
    user_id: str = Depends(get_current_user_id),
    tenant_id: str = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Mark all unread notifications as read for the current user."""
    try:
        service = _service(db)
        count = await service.mark_all_notifications_as_read(user_id=uuid.UUID(user_id))
        await db.commit()
        await _publish_notification_read(user_id, tenant_id, notification_ids=["*"])
        return {"status": "all_read", "marked_count": count}
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/bulk")
async def delete_notifications_bulk(
    older_than: str = Query("0d", description="Delete notifications older than: 1d, 3d, 7d, 30d, or 0d for all"),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Bulk-delete notifications older than a given period for the current user."""
    try:
        service = NotificationService(db)
        deleted_count = await service.delete_notifications_bulk(
            user_id=uuid.UUID(user_id),
            older_than=older_than,
        )
        await db.commit()
        return {"status": "deleted", "deleted": deleted_count}
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{notification_id}")
async def dismiss_notification(
    notification_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Dismiss (delete) a notification."""
    try:
        service = NotificationService(db)
        result = await service.delete_notification(
            notification_id=uuid.UUID(notification_id),
            user_id=uuid.UUID(user_id),
        )
        await db.commit()
        return result
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/settings")
async def get_notification_settings(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get the current user's notification delivery preferences."""
    try:
        service = NotificationService(db)
        settings = await service.get_user_notification_settings(user_id=uuid.UUID(user_id))
        return {
            "email_alerts": settings.email_alerts,
            "push_notifications": settings.push_notifications,
            "desktop_alerts": settings.desktop_alerts,
            "sound_alerts": settings.sound_alerts,
            "whatsapp_alerts": settings.whatsapp_alerts,
            "telegram_alerts": settings.telegram_alerts,
            "slack_alerts": settings.slack_alerts,
            "in_app_alerts": settings.in_app_alerts,
            "alert_threshold": settings.alert_threshold,
            "fraud_alert_email": settings.fraud_alert_email,
            "digest_frequency": settings.digest_frequency,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/settings")
async def update_notification_settings(
    settings: NotificationSettingsRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Update user notification delivery channel preferences.
    Accepts a flat object with boolean values for each channel.
    """
    try:
        service = NotificationService(db)

        update_kwargs: dict[str, Any] = {}
        if settings.email is not None:
            update_kwargs["email_alerts"] = settings.email
        if settings.whatsapp is not None:
            update_kwargs["whatsapp_alerts"] = settings.whatsapp
        if settings.telegram is not None:
            update_kwargs["telegram_alerts"] = settings.telegram
        if settings.slack is not None:
            update_kwargs["slack_alerts"] = settings.slack
        if settings.inApp is not None:
            update_kwargs["in_app_alerts"] = settings.inApp

        updated = await service.update_user_notification_settings(
            user_id=uuid.UUID(user_id),
            **update_kwargs,
        )
        await db.commit()

        return {
            "status": "updated",
            "email_alerts": updated.email_alerts,
            "whatsapp_alerts": updated.whatsapp_alerts,
            "telegram_alerts": updated.telegram_alerts,
            "slack_alerts": updated.slack_alerts,
            "in_app_alerts": updated.in_app_alerts,
        }
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e)) from e
