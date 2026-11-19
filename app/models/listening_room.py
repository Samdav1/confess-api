from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Index, JSON, Text, UniqueConstraint
from sqlmodel import SQLModel, Field


class ThoughtCategory(str, Enum):
    GENTLE_REMINDER = "gentle_reminder"
    QUIET_CONFESSION = "quiet_confession"
    LITTLE_JOYS = "little_joys"
    LATE_NIGHT_THOUGHT = "late_night_thought"
    RELATIONSHIPS = "relationships"
    FAMILY = "family"
    WORK = "work"
    PERSONAL = "personal"
    HEALTH = "health"
    GRIEF = "grief"
    IDENTITY = "identity"
    OTHER = "other"


class ThoughtTone(str, Enum):
    PINK = "pink"
    CREAM = "cream"
    PEACH = "peach"
    MAUVE = "mauve"
    WINE = "wine"


class ThoughtStatus(str, Enum):
    PENDING = "pending"
    PUBLISHED = "published"
    REJECTED = "rejected"
    HIDDEN = "hidden"
    REMOVED = "removed"
    FLAGGED = "flagged"
    DELETED = "deleted"


class ModerationStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class GentleWordStatus(str, Enum):
    PENDING = "pending"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    REMOVED = "removed"
    HIDDEN = "hidden"
    DELETED = "deleted"


class AcknowledgementType(str, Enum):
    I_HEAR_YOU = "i_hear_you"
    SENDING_WARMTH = "sending_warmth"
    YOU_ARE_NOT_ALONE = "you_are_not_alone"
    IM_LISTENING = "im_listening"
    YOURE_NOT_ALONE = "youre_not_alone"
    HOLDING_SPACE = "holding_space"


class LedgerEntryType(str, Enum):
    AWARD = "award"
    REDEMPTION = "redemption"
    REFUND = "refund"
    ADMIN_ADJUSTMENT = "admin_adjustment"
    REVERSAL = "reversal"
    EXPIRY = "expiry"


class DiscountType(str, Enum):
    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"


class RedemptionStatus(str, Enum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    USED = "used"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class ReportTargetType(str, Enum):
    THOUGHT = "thought"
    GENTLE_WORD = "gentle_word"


class ReportReason(str, Enum):
    HARASSMENT = "harassment"
    UNSAFE_ADVICE = "unsafe_advice"
    PERSONAL_INFORMATION = "personal_information"
    SPAM = "spam"
    THREAT = "threat"
    SELF_HARM = "self_harm"
    OTHER = "other"


class ReportStatus(str, Enum):
    PENDING = "pending"
    REVIEWED = "reviewed"
    ACTIONED = "actioned"
    DISMISSED = "dismissed"


class Thought(SQLModel, table=True):
    __tablename__ = "listening_room_thoughts"
    __table_args__ = (
        Index("ix_lr_thoughts_status_approved", "status", "approved_at", "id"),
        Index("ix_lr_thoughts_status_published", "status", "published_at", "id"),
        Index("ix_lr_thoughts_status_created", "status", "created_at"),
        Index("ix_lr_thoughts_cat_status_created", "category", "status", "created_at"),
        Index("ix_lr_thoughts_author_created", "author_user_id", "created_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    author_user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    author_label: str = Field(default="A quiet heart", nullable=False)
    body: str = Field(sa_column=Column(Text, nullable=False))
    category: Optional[str] = Field(default=None, nullable=True, index=True)
    tone: str = Field(default=ThoughtTone.CREAM.value, nullable=False)
    content_hash: Optional[str] = Field(default=None, nullable=True, index=True)
    status: str = Field(default=ThoughtStatus.PUBLISHED.value, nullable=False, index=True)
    moderation_status: str = Field(default=ModerationStatus.APPROVED.value, nullable=False, index=True)
    moderated_by: Optional[UUID] = Field(default=None, foreign_key="admins.id", nullable=True)
    moderated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    moderation_note: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    likes_count: int = Field(default=0, nullable=False)
    gentle_words_count: int = Field(default=0, nullable=False)
    is_urgent_flagged: bool = Field(default=False, nullable=False, index=True)
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc),
    )
    approved_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    published_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    deleted_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class ThoughtLike(SQLModel, table=True):
    __tablename__ = "listening_room_thought_likes"
    __table_args__ = (
        UniqueConstraint("thought_id", "user_id", name="uq_lr_thought_like_thought_user"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    thought_id: UUID = Field(foreign_key="listening_room_thoughts.id", nullable=False, index=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc),
    )


class GentleWord(SQLModel, table=True):
    __tablename__ = "listening_room_gentle_words"
    __table_args__ = (
        Index("ix_lr_gw_thought_status_created", "thought_id", "status", "created_at"),
        Index("ix_lr_gw_status_created", "status", "created_at"),
        Index("ix_lr_gw_author_created", "author_user_id", "created_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    thought_id: UUID = Field(foreign_key="listening_room_thoughts.id", nullable=False, index=True)
    author_user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    author_label: str = Field(default="A kind stranger", nullable=False)
    body: str = Field(sa_column=Column(Text, nullable=False))
    content_hash: Optional[str] = Field(default=None, nullable=True, index=True)
    status: str = Field(default=GentleWordStatus.PENDING_REVIEW.value, nullable=False, index=True)
    moderated_by: Optional[UUID] = Field(default=None, foreign_key="admins.id", nullable=True)
    moderated_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    moderation_note: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    points_awarded: int = Field(default=0, nullable=False)
    idempotency_key: Optional[str] = Field(default=None, nullable=True, unique=True, index=True)
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc),
    )
    approved_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    deleted_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class ThoughtAcknowledgement(SQLModel, table=True):
    __tablename__ = "listening_room_acknowledgements"
    __table_args__ = (
        UniqueConstraint("thought_id", "user_id", "acknowledgement_type", name="uq_lr_ack_thought_user_type"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    thought_id: UUID = Field(foreign_key="listening_room_thoughts.id", nullable=False, index=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    acknowledgement_type: str = Field(nullable=False)
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc),
    )


class KindnessPointsPolicy(SQLModel, table=True):
    __tablename__ = "kindness_points_policies"

    id: int = Field(default=1, primary_key=True)
    points_per_gentle_word: int = Field(default=10, nullable=False)
    minimum_points_to_redeem: int = Field(default=100, nullable=False)
    daily_award_cap: int = Field(default=50, nullable=False)
    weekly_award_cap: int = Field(default=200, nullable=False)
    require_thought_moderation: bool = Field(default=False, nullable=False)
    updated_by_admin_id: Optional[UUID] = Field(default=None, foreign_key="admins.id", nullable=True)
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc),
    )


class UserKindnessBalance(SQLModel, table=True):
    __tablename__ = "user_kindness_balances"

    user_id: UUID = Field(foreign_key="users.id", primary_key=True)
    balance: int = Field(default=0, nullable=False)
    total_earned: int = Field(default=0, nullable=False)
    total_redeemed: int = Field(default=0, nullable=False)
    posting_restricted_until: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    posting_restriction_reason: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    restricted_by_admin_id: Optional[UUID] = Field(default=None, foreign_key="admins.id", nullable=True)
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc),
    )


class KindnessPointsLedger(SQLModel, table=True):
    __tablename__ = "kindness_points_ledger"
    __table_args__ = (
        Index("ix_kp_ledger_user_created", "user_id", "created_at", "id"),
        Index("ix_kp_ledger_source", "source_type", "source_id"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    entry_type: str = Field(nullable=False, index=True)
    points_delta: int = Field(nullable=False)
    balance_after: int = Field(nullable=False)
    source_type: str = Field(nullable=False)
    source_id: Optional[UUID] = Field(default=None, nullable=True, index=True)
    idempotency_key: str = Field(nullable=False, unique=True, index=True)
    description: str = Field(nullable=False)
    created_by_admin_id: Optional[UUID] = Field(default=None, foreign_key="admins.id", nullable=True)
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
        default_factory=lambda: datetime.now(timezone.utc),
    )


class LittlePerk(SQLModel, table=True):
    __tablename__ = "little_perks"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(nullable=False)
    description: str = Field(sa_column=Column(Text, nullable=False))
    points_cost: int = Field(nullable=False)
    discount_type: str = Field(nullable=False)
    discount_value: float = Field(nullable=False)
    currency: Optional[str] = Field(default="NGN", nullable=True)
    eligible_celebration_types: List[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, default=[]),
    )
    minimum_order_amount: Optional[float] = Field(default=None, nullable=True)
    maximum_discount_amount: Optional[float] = Field(default=None, nullable=True)
    starts_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    ends_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    active: bool = Field(default=True, nullable=False, index=True)
    usage_limit_total: Optional[int] = Field(default=None, nullable=True)
    usage_limit_per_user: Optional[int] = Field(default=None, nullable=True)
    times_redeemed: int = Field(default=0, nullable=False)
    created_by: Optional[UUID] = Field(default=None, foreign_key="admins.id", nullable=True)
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc),
    )


class PerkRedemption(SQLModel, table=True):
    __tablename__ = "perk_redemptions"
    __table_args__ = (
        Index("ix_perk_redemptions_user_created", "user_id", "created_at"),
        Index("ix_perk_redemptions_status_expires", "status", "expires_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    perk_id: UUID = Field(foreign_key="little_perks.id", nullable=False, index=True)
    points_spent: int = Field(nullable=False)
    code: str = Field(nullable=False, unique=True, index=True)
    code_hash: str = Field(nullable=False, unique=True, index=True)
    status: str = Field(default=RedemptionStatus.AVAILABLE.value, nullable=False, index=True)
    celebration_id: Optional[UUID] = Field(
        default=None,
        foreign_key="celebration_pages.id",
        nullable=True,
        index=True,
    )
    payment_reference: Optional[str] = Field(default=None, nullable=True, index=True)
    discount_amount: Optional[float] = Field(default=None, nullable=True)
    original_order_amount: Optional[float] = Field(default=None, nullable=True)
    final_order_amount: Optional[float] = Field(default=None, nullable=True)
    currency: Optional[str] = Field(default="NGN", nullable=True)
    expires_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    redeemed_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc),
    )
    reserved_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    used_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc),
    )
    idempotency_key: str = Field(nullable=False, unique=True, index=True)


class ContentReport(SQLModel, table=True):
    __tablename__ = "content_reports"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    reporter_user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    target_type: str = Field(nullable=False, index=True)
    target_id: UUID = Field(nullable=False, index=True)
    reason: str = Field(nullable=False, index=True)
    details: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    status: str = Field(default=ReportStatus.PENDING.value, nullable=False, index=True)
    reviewed_by: Optional[UUID] = Field(default=None, foreign_key="admins.id", nullable=True)
    resolution_note: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    reviewed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc),
    )


class ModerationAuditLog(SQLModel, table=True):
    __tablename__ = "moderation_audit_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    admin_user_id: UUID = Field(foreign_key="admins.id", nullable=False, index=True)
    action: str = Field(nullable=False, index=True)
    target_type: str = Field(nullable=False, index=True)
    target_id: UUID = Field(nullable=False, index=True)
    previous_status: Optional[str] = Field(default=None, nullable=True)
    new_status: Optional[str] = Field(default=None, nullable=True)
    reason: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    metadata_: Optional[dict] = Field(
        default=None,
        sa_column=Column("metadata", JSON, nullable=True),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
        default_factory=lambda: datetime.now(timezone.utc),
    )
