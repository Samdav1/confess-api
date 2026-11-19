import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, List, Optional
from uuid import UUID

from fastapi import BackgroundTasks
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.sessions import engine
from app.models.notification import Notification, NotificationType

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    user_id: UUID
    notification_type: str
    title: str
    content: str
    reference_id: Optional[UUID] = None
    reference_type: Optional[str] = None
    metadata: Optional[dict] = None


class ListeningRoomNotifications:
    """Privacy-safe notification builders for The Listening Room & Care Desk."""

    @staticmethod
    def thought_submitted(author_user_id: UUID, thought_id: UUID) -> NotificationEvent:
        return NotificationEvent(
            user_id=author_user_id,
            notification_type=NotificationType.THOUGHT_SUBMITTED.value,
            title="Your Thought has been submitted",
            content="Your Thought was received and is awaiting Care Desk review before appearing in The Listening Room.",
            reference_id=thought_id,
            reference_type="thought",
            metadata={"status": "pending"},
        )

    @staticmethod
    def thought_published(author_user_id: UUID, thought_id: UUID) -> NotificationEvent:
        return NotificationEvent(
            user_id=author_user_id,
            notification_type=NotificationType.THOUGHT_PUBLISHED.value,
            title="Your Thought is now in The Listening Room",
            content="Your Thought has been approved and shared anonymously in The Listening Room.",
            reference_id=thought_id,
            reference_type="thought",
            metadata={"status": "published"},
        )

    @staticmethod
    def thought_rejected(author_user_id: UUID, thought_id: UUID) -> NotificationEvent:
        return NotificationEvent(
            user_id=author_user_id,
            notification_type=NotificationType.THOUGHT_REJECTED.value,
            title="Update on your Thought",
            content="Your Thought wasn't approved for The Listening Room this time. Please review the community guidelines and try again.",
            reference_id=thought_id,
            reference_type="thought",
            metadata={"status": "rejected"},
        )

    @staticmethod
    def gentle_word_submitted(
        author_user_id: UUID,
        gentle_word_id: UUID,
        thought_id: UUID,
    ) -> NotificationEvent:
        return NotificationEvent(
            user_id=author_user_id,
            notification_type=NotificationType.GENTLE_WORD_SUBMITTED.value,
            title="Your Gentle Word has been submitted",
            content="Your Gentle Word is queued for Care Desk review before it becomes visible.",
            reference_id=gentle_word_id,
            reference_type="gentle_word",
            metadata={"thought_id": str(thought_id), "status": "pending_review"},
        )

    @staticmethod
    def gentle_word_approved(
        author_user_id: UUID,
        gentle_word_id: UUID,
        thought_id: UUID,
        points_awarded: int,
    ) -> NotificationEvent:
        msg = (
            f"Your Gentle Word has been shared. You earned {points_awarded} Kindness Points."
            if points_awarded > 0
            else "Your Gentle Word has been shared in The Listening Room."
        )
        return NotificationEvent(
            user_id=author_user_id,
            notification_type=NotificationType.GENTLE_WORD_APPROVED.value,
            title="Your Gentle Word was approved",
            content=msg,
            reference_id=gentle_word_id,
            reference_type="gentle_word",
            metadata={"thought_id": str(thought_id), "points_awarded": points_awarded, "status": "approved"},
        )

    @staticmethod
    def gentle_word_received(
        thought_author_user_id: UUID,
        thought_id: UUID,
        gentle_word_id: UUID,
    ) -> NotificationEvent:
        # Privacy-safe: never exposes contributor identity
        return NotificationEvent(
            user_id=thought_author_user_id,
            notification_type=NotificationType.GENTLE_WORD_RECEIVED.value,
            title="Someone left a Gentle Word for your Thought",
            content="Someone left an approved Gentle Word for your Thought in The Listening Room.",
            reference_id=thought_id,
            reference_type="thought",
            metadata={"gentle_word_id": str(gentle_word_id)},
        )

    @staticmethod
    def gentle_word_rejected(
        author_user_id: UUID,
        gentle_word_id: UUID,
        thought_id: UUID,
    ) -> NotificationEvent:
        return NotificationEvent(
            user_id=author_user_id,
            notification_type=NotificationType.GENTLE_WORD_REJECTED.value,
            title="Update on your Gentle Word",
            content="Your Gentle Word wasn't approved this time. You can review the community guidelines and try again.",
            reference_id=gentle_word_id,
            reference_type="gentle_word",
            metadata={"thought_id": str(thought_id), "status": "rejected"},
        )

    @staticmethod
    def content_hidden(
        author_user_id: UUID,
        target_type: str,
        target_id: UUID,
    ) -> NotificationEvent:
        label = "Thought" if target_type == "thought" else "Gentle Word"
        return NotificationEvent(
            user_id=author_user_id,
            notification_type=NotificationType.CONTENT_HIDDEN.value,
            title=f"Your {label} was hidden",
            content=f"One of your {label}s in The Listening Room was hidden following moderation review.",
            reference_id=target_id,
            reference_type=target_type,
            metadata={"status": "hidden"},
        )

    @staticmethod
    def perk_redeemed(
        user_id: UUID,
        redemption_id: UUID,
        perk_id: UUID,
        perk_name: str,
    ) -> NotificationEvent:
        return NotificationEvent(
            user_id=user_id,
            notification_type=NotificationType.PERK_REDEEMED.value,
            title="Your Little Perk is ready to use",
            content=f"You redeemed '{perk_name}'. Apply it when paying for an eligible celebration.",
            reference_id=redemption_id,
            reference_type="perk_redemption",
            metadata={"perk_id": str(perk_id)},
        )


def _default_session_factory() -> AsyncSession:
    return AsyncSession(engine, expire_on_commit=False)


class NotificationDispatcher:
    """
    Non-blocking background dispatcher for in-app notifications.
    Uses independent pooled DB sessions outside the request transaction so
    notifications never block the request lifecycle or fail on closed sessions.
    """

    def enqueue(
        self,
        background_tasks: Optional[BackgroundTasks],
        events: List[NotificationEvent],
    ) -> None:
        valid_events = [e for e in events if e is not None]
        if not valid_events:
            return
        if background_tasks is not None:
            background_tasks.add_task(self.dispatch_batch, valid_events)
        else:
            logger.debug(
                "Skipped background notification enqueue without BackgroundTasks for %d event(s)",
                len(valid_events),
            )

    async def dispatch_batch(
        self,
        events: List[NotificationEvent],
        session_factory: Optional[Callable[[], AsyncSession]] = None,
    ) -> int:
        if not events:
            return 0

        make_session = session_factory or _default_session_factory
        backoff_seconds = (0.25, 0.5, 1.0)

        for attempt, delay in enumerate(backoff_seconds, start=1):
            try:
                async with make_session() as session:
                    records = [
                        Notification(
                            user_id=ev.user_id,
                            type=ev.notification_type,
                            title=ev.title,
                            content=ev.content,
                            reference_id=ev.reference_id,
                            reference_type=ev.reference_type,
                            metadata_=ev.metadata,
                        )
                        for ev in events
                    ]
                    session.add_all(records)
                    await session.commit()
                    return len(records)
            except Exception as exc:
                if attempt < len(backoff_seconds):
                    logger.warning(
                        "Notification batch dispatch attempt %d/%d failed (%s); retrying in %.2fs",
                        attempt,
                        len(backoff_seconds),
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "Failed to dispatch %d in-app notification(s) after %d attempts: %s",
                        len(events),
                        len(backoff_seconds),
                        exc,
                        exc_info=True,
                    )
        return 0


notification_dispatcher = NotificationDispatcher()
