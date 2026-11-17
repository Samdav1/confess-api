from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4
from enum import Enum
from sqlalchemy import Column, DateTime, JSON
from sqlmodel import SQLModel, Field, Relationship


class NotificationType(str, Enum):
    ANONYMOUS_MESSAGE = "anonymous_message"
    PAYMENT = "payment"
    SYSTEM = "system"
    CONFESS_FORM = "confess_form"
    THOUGHT_PUBLISHED = "thought_published"
    GENTLE_WORD_APPROVED = "gentle_word_approved"
    GENTLE_WORD_REJECTED = "gentle_word_rejected"
    GENTLE_WORD_RECEIVED = "gentle_word_received"
    POINTS_AWARDED = "points_awarded"
    PERK_REDEEMED = "perk_redeemed"
    REDEMPTION_EXPIRING = "redemption_expiring"
    CONTENT_HIDDEN = "content_hidden"


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True, nullable=False)

    type: str = Field(nullable=False, index=True)  # NotificationType value
    title: str = Field(nullable=False)
    content: str = Field(nullable=False)

    is_read: bool = Field(default=False, index=True)
    read_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True)
    )

    # Generic reference to any related entity
    reference_id: Optional[UUID] = Field(default=None, nullable=True, index=True)
    reference_type: Optional[str] = Field(default=None, nullable=True)

    # Flexible extra data
    metadata_: Optional[dict] = Field(
        default=None,
        sa_column=Column("metadata", JSON, nullable=True)
    )

    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc)
    )

    user: Optional["User"] = Relationship(back_populates="notifications")
