
# ============================================================
# PaySentinelIQ — Notification Service
# Handles creation, retrieval, and management of user notifications.
# This service interacts with the database models and orchestrates
# background tasks for sending notifications to various channels.
# ============================================================

import uuid
from typing import List, Optional, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, and_, delete
from datetime import datetime, timedelta, timezone

from app.shared.orm_models import NotificationModel, UserSettingsModel
from app.shared.exceptions import NotFoundError
from app.shared.repository import BaseRepository

class NotificationRepository(BaseRepository[NotificationModel]):
    def __init__(self, session: AsyncSession):
        super().__init__(NotificationModel, session)

class NotificationService:
    def __init__(self, session: AsyncSession, event_publisher=None):
        self.session = session
        self.repository = NotificationRepository(session)
        # Optional EventPublisher — publishes `notification.created` for the
        # activity history. Injected by callers (API/workers); the concrete
        # RabbitMQ implementation is NEVER imported here.
        self.event_publisher = event_publisher

    async def create_notification(
        self,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        type: str,
        title: str,
        message: str,
        severity: str = "normal",
        action_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> NotificationModel:
        """
        Creates a new notification record in the database.
        """
        notification = NotificationModel(
            user_id=user_id,
            tenant_id=tenant_id,
            type=type,
            title=title,
            message=message,
            severity=severity,
            action_url=action_url,
            metadata_=metadata,
        )
        self.session.add(notification)
        await self.session.flush() # Flush to get the ID, but don't commit yet

        # ── Feed the activity history (async consumers) ──
        if self.event_publisher is not None:
            await self._publish_notification_created(notification)

        return notification

    async def _publish_notification_created(self, notification: NotificationModel) -> None:
        """Publish notification.created (non-fatal — never breaks creation)."""
        try:
            from app.messaging.domain.envelope import new_event
            from app.messaging.domain.event_types import EventType

            event = new_event(
                EventType.NOTIFICATION_CREATED.value,
                payload={
                    "notification_id": str(notification.id),
                    "notification_type": notification.type,
                },
                user_id=str(notification.user_id),
                tenant_id=str(notification.tenant_id),
            )
            await self.event_publisher.publish(event)
        except Exception as exc:  # pragma: no cover - audit must never fail create
            import logging

            logging.getLogger(__name__).warning(
                "notification.created publish failed (non-fatal): %s", exc
            )

    async def get_notifications_for_user(
        self,
        user_id: uuid.UUID,
        skip: int = 0,
        limit: int = 100,
        is_read: Optional[bool] = None,
        severity: Optional[str] = None,
        type: Optional[str] = None,
    ) -> List[NotificationModel]:
        """
        Retrieves notifications for a specific user with optional filtering and pagination.
        """
        query = select(NotificationModel).where(NotificationModel.user_id == user_id)

        if is_read is not None:
            query = query.where(NotificationModel.is_read == is_read)
        if severity:
            query = query.where(NotificationModel.severity == severity)
        if type:
            query = query.where(NotificationModel.type == type)

        query = query.order_by(desc(NotificationModel.created_at)).offset(skip).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_unread_notification_count(self, user_id: uuid.UUID) -> int:
        """
        Returns the count of unread notifications for a specific user.
        """
        query = select(func.count(NotificationModel.id)).where(
            and_(
                NotificationModel.user_id == user_id,
                NotificationModel.is_read == False,
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one()

    async def mark_notification_as_read(self, notification_id: uuid.UUID, user_id: uuid.UUID) -> NotificationModel:
        """
        Marks a single notification as read.
        """
        notification = await self.repository.get(notification_id)
        if not notification or notification.user_id != user_id:
            raise NotFoundError("Notification", str(notification_id))
        
        notification.is_read = True
        self.session.add(notification)
        await self.session.flush()
        return notification

    async def mark_notification_as_unread(self, notification_id: uuid.UUID, user_id: uuid.UUID) -> NotificationModel:
        """
        Marks a single notification as unread.
        """
        notification = await self.repository.get(notification_id)
        if not notification or notification.user_id != user_id:
            raise NotFoundError("Notification", str(notification_id))
        
        notification.is_read = False
        self.session.add(notification)
        await self.session.flush()
        return notification

    async def mark_all_notifications_as_read(self, user_id: uuid.UUID) -> int:
        """
        Marks all unread notifications for a user as read.
        Returns the number of notifications marked as read.
        """
        query = select(NotificationModel).where(
            and_(
                NotificationModel.user_id == user_id,
                NotificationModel.is_read == False,
            )
        )
        result = await self.session.execute(query)
        unread_notifications = list(result.scalars().all())

        for notification in unread_notifications:
            notification.is_read = True
            self.session.add(notification)
        
        await self.session.flush()
        return len(unread_notifications)

    async def delete_notification(self, notification_id: uuid.UUID, user_id: uuid.UUID):
        """
        Deletes a notification.
        """
        notification = await self.repository.get(notification_id)
        if not notification or notification.user_id != user_id:
            raise NotFoundError("Notification", str(notification_id))
        
        await self.session.delete(notification)
        await self.session.flush()
        return {"message": "Notification deleted successfully"}
    
    async def get_user_notification_settings(self, user_id: uuid.UUID) -> UserSettingsModel:
        """
        Retrieves user notification settings.
        """
        query = select(UserSettingsModel).where(UserSettingsModel.user_id == user_id)
        result = await self.session.execute(query)
        settings = result.scalar_one_or_none()
        if not settings:
            raise NotFoundError("UserSettings", str(user_id))
        return settings

    async def update_user_notification_settings(
        self,
        user_id: uuid.UUID,
        email_alerts: Optional[bool] = None,
        push_notifications: Optional[bool] = None,
        desktop_alerts: Optional[bool] = None,
        sound_alerts: Optional[bool] = None,
        whatsapp_alerts: Optional[bool] = None,
        telegram_alerts: Optional[bool] = None,
        slack_alerts: Optional[bool] = None,
        in_app_alerts: Optional[bool] = None,
        alert_threshold: Optional[int] = None,
        fraud_alert_email: Optional[str] = None,
        digest_frequency: Optional[str] = None,
    ) -> UserSettingsModel:
        """
        Updates user notification settings.
        """
        settings = await self.get_user_notification_settings(user_id) # Reuse existing getter
        
        if email_alerts is not None:
            settings.email_alerts = email_alerts
        if push_notifications is not None:
            settings.push_notifications = push_notifications
        if desktop_alerts is not None:
            settings.desktop_alerts = desktop_alerts
        if sound_alerts is not None:
            settings.sound_alerts = sound_alerts
        if whatsapp_alerts is not None:
            settings.whatsapp_alerts = whatsapp_alerts
        if telegram_alerts is not None:
            settings.telegram_alerts = telegram_alerts
        if slack_alerts is not None:
            settings.slack_alerts = slack_alerts
        if in_app_alerts is not None:
            settings.in_app_alerts = in_app_alerts
        if alert_threshold is not None:
            settings.alert_threshold = alert_threshold
        if fraud_alert_email is not None:
            settings.fraud_alert_email = fraud_alert_email
        if digest_frequency is not None:
            settings.digest_frequency = digest_frequency

        self.session.add(settings)
        await self.session.flush()
        return settings

    async def delete_notifications_bulk(
        self,
        user_id: uuid.UUID,
        older_than: str = "0d",
    ) -> int:
        """Delete notifications older than a given period for a user.

        Args:
            user_id: The user whose notifications to delete.
            older_than: Period string like '1d', '3d', '7d', '30d', or '0d' for all.

        Returns:
            Number of notifications deleted.
        """
        # Parse the older_than period into a timedelta
        period_map = {"d": 1, "w": 7, "m": 30}
        unit = older_than[-1].lower() if older_than else "d"
        multiplier = period_map.get(unit, 1)
        try:
            days = int(older_than[:-1]) if len(older_than) > 1 else int(older_than)
        except (ValueError, TypeError):
            days = 0

        conditions = [NotificationModel.user_id == user_id]

        if days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days * multiplier)
            conditions.append(NotificationModel.created_at < cutoff)

        stmt = delete(NotificationModel).where(and_(*conditions))
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount
