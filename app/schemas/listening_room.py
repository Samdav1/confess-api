from datetime import datetime
from typing import Any, Dict, Generic, List, Optional, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.listening_room import (
    AcknowledgementType,
    DiscountType,
    GentleWordStatus,
    LedgerEntryType,
    ModerationStatus,
    RedemptionStatus,
    ReportReason,
    ReportStatus,
    ReportTargetType,
    ThoughtCategory,
    ThoughtStatus,
    ThoughtTone,
)

T = TypeVar("T")


class CamelModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class DataResponse(CamelModel, Generic[T]):
    data: T


# --- Thought & Gentle Word Schemas ---


class ThoughtCreateRequest(CamelModel):
    body: Optional[str] = Field(default=None, max_length=3000)
    content: Optional[str] = Field(default=None, max_length=3000)
    category: Optional[ThoughtCategory] = None
    author_label: Optional[str] = Field(default="A quiet heart", max_length=60, alias="authorLabel")
    tone: Optional[ThoughtTone] = Field(default=ThoughtTone.CREAM)
    accept_guidelines: bool = Field(default=True, alias="acceptGuidelines")

    @model_validator(mode="before")
    @classmethod
    def normalize_content_or_body(cls, values: Any) -> Any:
        if isinstance(values, dict):
            raw = values.get("body") if values.get("body") is not None else values.get("content")
            if raw is not None:
                values["body"] = raw
                values["content"] = raw
        return values

    @field_validator("body")
    @classmethod
    def validate_non_empty_body(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        stripped = " ".join(v.strip().split())
        if len(stripped) < 5:
            raise ValueError("Thought must contain at least 5 non-whitespace characters.")
        return v.strip()

    @model_validator(mode="after")
    def require_body_or_content(self) -> "ThoughtCreateRequest":
        text = self.body or self.content
        if not text or len(" ".join(text.strip().split())) < 5:
            raise ValueError("Thought content must contain at least 5 non-whitespace characters.")
        self.body = text.strip()
        self.content = self.body
        if not self.author_label or not self.author_label.strip():
            self.author_label = "A quiet heart"
        else:
            self.author_label = self.author_label.strip()
        return self

    @field_validator("accept_guidelines")
    @classmethod
    def validate_guidelines(cls, v: bool) -> bool:
        if not v:
            raise ValueError("You must accept community guidelines to share a Thought.")
        return v


class PublicGentleWordItem(CamelModel):
    id: UUID
    thought_id: UUID = Field(alias="thoughtId")
    author_label: str = Field(default="A kind stranger", alias="authorLabel")
    body: str
    content: Optional[str] = None
    created_at: datetime = Field(alias="createdAt")
    approved_at: Optional[datetime] = Field(default=None, alias="approvedAt")

    @model_validator(mode="after")
    def sync_content(self) -> "PublicGentleWordItem":
        if self.content is None:
            self.content = self.body
        return self


class PublicGentleWordsListResponse(CamelModel):
    data: List[PublicGentleWordItem]
    items: List[PublicGentleWordItem]
    total: int = 0
    page: int = 1
    page_size: int = Field(default=20, alias="pageSize")


class PublicThoughtItem(CamelModel):
    id: UUID
    author_label: str = Field(default="A quiet heart", alias="authorLabel")
    body: str
    content: Optional[str] = None
    category: Optional[str] = None
    tone: str = ThoughtTone.CREAM.value
    status: str = ThoughtStatus.PUBLISHED.value
    created_at: datetime = Field(alias="createdAt")
    approved_at: Optional[datetime] = Field(default=None, alias="approvedAt")
    published_at: Optional[datetime] = Field(default=None, alias="publishedAt")
    likes_count: int = Field(default=0, alias="likesCount")
    gentle_words_count: int = Field(default=0, alias="gentleWordsCount")
    is_liked: bool = Field(default=False, alias="isLiked")
    acknowledgements: List[str] = Field(default_factory=list)
    user_acknowledgements: List[str] = Field(default_factory=list, alias="userAcknowledgements")
    acknowledgements_count: Dict[str, int] = Field(default_factory=dict, alias="acknowledgementsCount")

    @model_validator(mode="after")
    def sync_content(self) -> "PublicThoughtItem":
        if self.content is None:
            self.content = self.body
        return self


class PublicThoughtDetailItem(PublicThoughtItem):
    gentle_words: List[PublicGentleWordItem] = Field(default_factory=list, alias="gentleWords")
    support_resources: Optional[Dict[str, Any]] = Field(default=None, alias="supportResources")


class OwnThoughtItem(CamelModel):
    id: UUID
    author_label: str = Field(default="A quiet heart", alias="authorLabel")
    body: str
    content: Optional[str] = None
    category: Optional[str] = None
    tone: str = ThoughtTone.CREAM.value
    status: str
    moderation_status: str = Field(alias="moderationStatus")
    likes_count: int = Field(default=0, alias="likesCount")
    gentle_words_count: int = Field(default=0, alias="gentleWordsCount")
    created_at: datetime = Field(alias="createdAt")
    approved_at: Optional[datetime] = Field(default=None, alias="approvedAt")
    published_at: Optional[datetime] = Field(default=None, alias="publishedAt")
    support_resources: Optional[Dict[str, Any]] = Field(default=None, alias="supportResources")

    @model_validator(mode="after")
    def sync_content(self) -> "OwnThoughtItem":
        if self.content is None:
            self.content = self.body
        return self


class ThoughtFeedResponse(CamelModel):
    data: List[PublicThoughtItem]
    items: List[PublicThoughtItem] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = Field(default=20, alias="pageSize")
    next_cursor: Optional[str] = Field(default=None, alias="nextCursor")

    @model_validator(mode="after")
    def sync_items(self) -> "ThoughtFeedResponse":
        if not self.items and self.data:
            self.items = self.data
        return self


class ThoughtLikeToggleResponse(CamelModel):
    thought_id: UUID = Field(alias="thoughtId")
    liked: bool
    likes_count: int = Field(alias="likesCount")


class OwnThoughtsResponse(CamelModel):
    data: List[OwnThoughtItem]
    items: List[OwnThoughtItem] = Field(default_factory=list)
    next_cursor: Optional[str] = Field(default=None, alias="nextCursor")

    @model_validator(mode="after")
    def sync_items(self) -> "OwnThoughtsResponse":
        if not self.items and self.data:
            self.items = self.data
        return self


class GentleWordCreateRequest(CamelModel):
    body: Optional[str] = Field(default=None, max_length=1500)
    content: Optional[str] = Field(default=None, max_length=1500)
    author_label: Optional[str] = Field(default="A kind stranger", max_length=60, alias="authorLabel")
    idempotency_key: Optional[str] = Field(default=None, max_length=128, alias="idempotencyKey")

    @model_validator(mode="before")
    @classmethod
    def normalize_content_or_body(cls, values: Any) -> Any:
        if isinstance(values, dict):
            raw = values.get("body") if values.get("body") is not None else values.get("content")
            if raw is not None:
                values["body"] = raw
                values["content"] = raw
        return values

    @model_validator(mode="after")
    def require_body_or_content(self) -> "GentleWordCreateRequest":
        text = self.body or self.content
        if not text or len(" ".join(text.strip().split())) < 5:
            raise ValueError("A Gentle Word must contain at least 5 non-whitespace characters.")
        self.body = text.strip()
        self.content = self.body
        if not self.author_label or not self.author_label.strip():
            self.author_label = "A kind stranger"
        else:
            self.author_label = self.author_label.strip()
        return self


class GentleWordCreateItem(CamelModel):
    id: UUID
    thought_id: UUID = Field(alias="thoughtId")
    author_label: str = Field(default="A kind stranger", alias="authorLabel")
    body: str
    content: Optional[str] = None
    status: str
    created_at: datetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def sync_content(self) -> "GentleWordCreateItem":
        if self.content is None:
            self.content = self.body
        return self


class OwnGentleWordItem(CamelModel):
    id: UUID
    thought_id: UUID = Field(alias="thoughtId")
    author_label: str = Field(default="A kind stranger", alias="authorLabel")
    body: str
    content: Optional[str] = None
    status: str
    points_awarded: int = Field(default=0, alias="pointsAwarded")
    created_at: datetime = Field(alias="createdAt")
    approved_at: Optional[datetime] = Field(default=None, alias="approvedAt")

    @model_validator(mode="after")
    def sync_content(self) -> "OwnGentleWordItem":
        if self.content is None:
            self.content = self.body
        return self


class OwnGentleWordsResponse(CamelModel):
    data: List[OwnGentleWordItem]
    items: List[OwnGentleWordItem] = Field(default_factory=list)
    next_cursor: Optional[str] = Field(default=None, alias="nextCursor")

    @model_validator(mode="after")
    def sync_items(self) -> "OwnGentleWordsResponse":
        if not self.items and self.data:
            self.items = self.data
        return self


class AcknowledgementCreateRequest(CamelModel):
    type: Optional[AcknowledgementType] = None
    acknowledgement_type: Optional[AcknowledgementType] = Field(default=None, alias="acknowledgementType")

    @model_validator(mode="after")
    def resolve_type(self) -> "AcknowledgementCreateRequest":
        resolved = self.type or self.acknowledgement_type
        if not resolved:
            raise ValueError("Acknowledgement type is required.")
        self.type = resolved
        self.acknowledgement_type = resolved
        return self


class AcknowledgementResponseItem(CamelModel):
    thought_id: UUID = Field(alias="thoughtId")
    type: Optional[str] = None
    toggled: Optional[bool] = None
    acknowledgements: List[str]
    user_acknowledgements: List[str] = Field(default_factory=list, alias="userAcknowledgements")
    acknowledgements_count: Dict[str, int] = Field(default_factory=dict, alias="acknowledgementsCount")


# --- Reports ---


class ContentReportCreateRequest(CamelModel):
    target_type: ReportTargetType = Field(alias="targetType")
    target_id: UUID = Field(alias="targetId")
    reason: ReportReason
    details: Optional[str] = Field(default=None, max_length=1000)


class ThoughtReportCreateRequest(CamelModel):
    reason: ReportReason
    details: Optional[str] = Field(default=None, max_length=1000)


class ContentReportResponseItem(CamelModel):
    id: UUID
    target_type: str = Field(alias="targetType")
    target_id: UUID = Field(alias="targetId")
    reason: str
    status: str
    created_at: datetime = Field(alias="createdAt")


# --- Kindness Points & Little Perks ---


class LedgerEntryItem(CamelModel):
    id: UUID
    type: str
    points_delta: int = Field(alias="pointsDelta")
    balance_after: int = Field(alias="balanceAfter")
    source_type: str = Field(alias="sourceType")
    description: str
    created_at: datetime = Field(alias="createdAt")


class KindnessPointsBalanceData(CamelModel):
    balance: int
    total_earned: int = Field(alias="totalEarned")
    total_redeemed: int = Field(alias="totalRedeemed")
    minimum_points_to_redeem: int = Field(alias="minimumPointsToRedeem")
    entries: List[LedgerEntryItem] = Field(default_factory=list)
    next_cursor: Optional[str] = Field(default=None, alias="nextCursor")


class LittlePerkPublicItem(CamelModel):
    id: UUID
    name: str
    description: str
    points_cost: int = Field(alias="pointsCost")
    discount_type: str = Field(alias="discountType")
    discount_value: float = Field(alias="discountValue")
    currency: Optional[str] = None
    eligible_celebration_types: List[str] = Field(default_factory=list, alias="eligibleCelebrationTypes")
    minimum_order_amount: Optional[float] = Field(default=None, alias="minimumOrderAmount")
    maximum_discount_amount: Optional[float] = Field(default=None, alias="maximumDiscountAmount")
    starts_at: Optional[datetime] = Field(default=None, alias="startsAt")
    ends_at: Optional[datetime] = Field(default=None, alias="endsAt")
    active: bool


class RedeemPerkRequest(CamelModel):
    idempotency_key: str = Field(..., min_length=8, max_length=128, alias="idempotencyKey")


class PerkRedemptionItem(CamelModel):
    id: UUID
    perk_id: UUID = Field(alias="perkId")
    perk_name: Optional[str] = Field(default=None, alias="perkName")
    points_spent: int = Field(alias="pointsSpent")
    code: str
    status: str
    discount_amount: Optional[float] = Field(default=None, alias="discountAmount")
    currency: Optional[str] = None
    celebration_id: Optional[UUID] = Field(default=None, alias="celebrationId")
    expires_at: Optional[datetime] = Field(default=None, alias="expiresAt")
    redeemed_at: datetime = Field(alias="redeemedAt")
    created_at: datetime = Field(alias="createdAt")


class ApplyRedemptionRequest(CamelModel):
    celebration_id: UUID = Field(alias="celebrationId")
    idempotency_key: Optional[str] = Field(default=None, max_length=128, alias="idempotencyKey")


class ApplyRedemptionResponse(CamelModel):
    redemption_id: UUID = Field(alias="redemptionId")
    code: str
    status: str
    celebration_id: UUID = Field(alias="celebrationId")
    original_amount: float = Field(alias="originalAmount")
    discount_amount: float = Field(alias="discountAmount")
    final_amount: float = Field(alias="finalAmount")
    currency: str


class ConsumeOrReleaseRedemptionRequest(CamelModel):
    idempotency_key: Optional[str] = Field(default=None, max_length=128, alias="idempotencyKey")
    payment_reference: Optional[str] = Field(default=None, max_length=128, alias="paymentReference")
    reason: Optional[str] = Field(default=None, max_length=500)


# --- Care Desk (Admin) Schemas ---


class AdminThoughtQueueItem(CamelModel):
    id: UUID
    author_user_id: UUID = Field(alias="authorUserId")
    author_label: str = Field(default="A quiet heart", alias="authorLabel")
    body: str
    content: Optional[str] = None
    category: Optional[str] = None
    tone: str = ThoughtTone.CREAM.value
    status: str
    moderation_status: str = Field(alias="moderationStatus")
    likes_count: int = Field(default=0, alias="likesCount")
    gentle_words_count: int = Field(default=0, alias="gentleWordsCount")
    is_urgent_flagged: bool = Field(alias="isUrgentFlagged")
    moderation_note: Optional[str] = Field(default=None, alias="moderationNote")
    moderated_by: Optional[UUID] = Field(default=None, alias="moderatedBy")
    moderated_at: Optional[datetime] = Field(default=None, alias="moderatedAt")
    created_at: datetime = Field(alias="createdAt")
    approved_at: Optional[datetime] = Field(default=None, alias="approvedAt")
    published_at: Optional[datetime] = Field(default=None, alias="publishedAt")

    @model_validator(mode="after")
    def sync_content(self) -> "AdminThoughtQueueItem":
        if self.content is None:
            self.content = self.body
        return self


class AdminGentleWordQueueItem(CamelModel):
    id: UUID
    thought_id: UUID = Field(alias="thoughtId")
    thought_preview: Optional[str] = Field(default=None, alias="thoughtPreview")
    author_user_id: UUID = Field(alias="authorUserId")
    author_label: str = Field(default="A kind stranger", alias="authorLabel")
    body: str
    content: Optional[str] = None
    status: str
    points_awarded: int = Field(alias="pointsAwarded")
    moderation_note: Optional[str] = Field(default=None, alias="moderationNote")
    rejection_reason: Optional[str] = Field(default=None, alias="rejectionReason")
    moderated_by: Optional[UUID] = Field(default=None, alias="moderatedBy")
    reviewed_by: Optional[UUID] = Field(default=None, alias="reviewedBy")
    moderated_at: Optional[datetime] = Field(default=None, alias="moderatedAt")
    reviewed_at: Optional[datetime] = Field(default=None, alias="reviewedAt")
    created_at: datetime = Field(alias="createdAt")
    approved_at: Optional[datetime] = Field(default=None, alias="approvedAt")

    @model_validator(mode="after")
    def sync_fields(self) -> "AdminGentleWordQueueItem":
        if self.content is None:
            self.content = self.body
        if self.rejection_reason is None and self.status == GentleWordStatus.REJECTED.value:
            self.rejection_reason = self.moderation_note
        if self.reviewed_by is None:
            self.reviewed_by = self.moderated_by
        if self.reviewed_at is None:
            self.reviewed_at = self.moderated_at
        return self


class AdminQueuePaginatedResponse(CamelModel, Generic[T]):
    total: int
    items: List[T]


class ModerationDecisionRequest(CamelModel):
    note: Optional[str] = Field(default=None, max_length=1000)
    reason: Optional[str] = Field(default=None, max_length=1000)
    rejection_reason: Optional[str] = Field(default=None, max_length=1000, alias="rejectionReason")

    @model_validator(mode="after")
    def sync_reason(self) -> "ModerationDecisionRequest":
        if self.reason is None and self.rejection_reason is not None:
            self.reason = self.rejection_reason
        return self


class HideContentRequest(CamelModel):
    reason: str = Field(..., min_length=3, max_length=1000)
    reverse_points: bool = Field(default=False, alias="reversePoints")


class AdminReportItem(CamelModel):
    id: UUID
    reporter_user_id: UUID = Field(alias="reporterUserId")
    target_type: str = Field(alias="targetType")
    target_id: UUID = Field(alias="targetId")
    target_preview: Optional[str] = Field(default=None, alias="targetPreview")
    reason: str
    details: Optional[str] = None
    status: str
    reviewed_by: Optional[UUID] = Field(default=None, alias="reviewedBy")
    resolution_note: Optional[str] = Field(default=None, alias="resolutionNote")
    reviewed_at: Optional[datetime] = Field(default=None, alias="reviewedAt")
    created_at: datetime = Field(alias="createdAt")


class ResolveReportRequest(CamelModel):
    status: ReportStatus
    note: Optional[str] = Field(default=None, max_length=1000)
    hide_target_content: bool = Field(default=False, alias="hideTargetContent")
    reverse_points: bool = Field(default=False, alias="reversePoints")


class PostingRestrictionRequest(CamelModel):
    restricted: bool
    duration_hours: Optional[int] = Field(default=24, ge=1, le=8760, alias="durationHours")
    reason: str = Field(..., min_length=3, max_length=1000)


class PostingRestrictionResponse(CamelModel):
    user_id: UUID = Field(alias="userId")
    restricted: bool
    posting_restricted_until: Optional[datetime] = Field(default=None, alias="postingRestrictedUntil")
    reason: Optional[str] = None


class PointsAdjustRequest(CamelModel):
    user_id: UUID = Field(alias="userId")
    points_delta: int = Field(alias="pointsDelta")
    reason: str = Field(..., min_length=3, max_length=500)
    idempotency_key: str = Field(..., min_length=8, max_length=128, alias="idempotencyKey")

    @field_validator("points_delta")
    @classmethod
    def validate_nonzero_delta(cls, v: int) -> int:
        if v == 0:
            raise ValueError("points_delta cannot be zero.")
        return v


class LittlePerkCreateRequest(CamelModel):
    name: str = Field(..., min_length=3, max_length=120)
    description: str = Field(..., min_length=5, max_length=1000)
    points_cost: int = Field(..., gt=0, alias="pointsCost")
    discount_type: DiscountType = Field(alias="discountType")
    discount_value: float = Field(..., gt=0, alias="discountValue")
    currency: Optional[str] = Field(default="NGN", min_length=3, max_length=3)
    eligible_celebration_types: List[str] = Field(default_factory=list, alias="eligibleCelebrationTypes")
    minimum_order_amount: Optional[float] = Field(default=None, ge=0, alias="minimumOrderAmount")
    maximum_discount_amount: Optional[float] = Field(default=None, gt=0, alias="maximumDiscountAmount")
    starts_at: Optional[datetime] = Field(default=None, alias="startsAt")
    ends_at: Optional[datetime] = Field(default=None, alias="endsAt")
    active: bool = True
    usage_limit_total: Optional[int] = Field(default=None, gt=0, alias="usageLimitTotal")
    usage_limit_per_user: Optional[int] = Field(default=None, gt=0, alias="usageLimitPerUser")


class LittlePerkUpdateRequest(CamelModel):
    name: Optional[str] = Field(default=None, min_length=3, max_length=120)
    description: Optional[str] = Field(default=None, min_length=5, max_length=1000)
    points_cost: Optional[int] = Field(default=None, gt=0, alias="pointsCost")
    discount_value: Optional[float] = Field(default=None, gt=0, alias="discountValue")
    eligible_celebration_types: Optional[List[str]] = Field(default=None, alias="eligibleCelebrationTypes")
    minimum_order_amount: Optional[float] = Field(default=None, ge=0, alias="minimumOrderAmount")
    maximum_discount_amount: Optional[float] = Field(default=None, gt=0, alias="maximumDiscountAmount")
    starts_at: Optional[datetime] = Field(default=None, alias="startsAt")
    ends_at: Optional[datetime] = Field(default=None, alias="endsAt")
    active: Optional[bool] = None
    usage_limit_total: Optional[int] = Field(default=None, gt=0, alias="usageLimitTotal")
    usage_limit_per_user: Optional[int] = Field(default=None, gt=0, alias="usageLimitPerUser")


class KindnessPointsPolicyResponse(CamelModel):
    points_per_gentle_word: int = Field(alias="pointsPerGentleWord")
    minimum_points_to_redeem: int = Field(alias="minimumPointsToRedeem")
    daily_award_cap: int = Field(alias="dailyAwardCap")
    weekly_award_cap: int = Field(alias="weeklyAwardCap")
    require_thought_moderation: bool = Field(alias="requireThoughtModeration")
    updated_at: datetime = Field(alias="updatedAt")


class KindnessPointsPolicyUpdateRequest(CamelModel):
    points_per_gentle_word: Optional[int] = Field(default=None, ge=0, alias="pointsPerGentleWord")
    minimum_points_to_redeem: Optional[int] = Field(default=None, ge=1, alias="minimumPointsToRedeem")
    daily_award_cap: Optional[int] = Field(default=None, ge=0, alias="dailyAwardCap")
    weekly_award_cap: Optional[int] = Field(default=None, ge=0, alias="weeklyAwardCap")
    require_thought_moderation: Optional[bool] = Field(default=None, alias="requireThoughtModeration")


class ListeningRoomAnalyticsResponse(CamelModel):
    weekly_active_contributors: int = Field(alias="weeklyActiveContributors")
    monthly_active_contributors: int = Field(alias="monthlyActiveContributors")
    thoughts_total: int = Field(alias="thoughtsTotal")
    thoughts_published: int = Field(alias="thoughtsPublished")
    thoughts_pending: int = Field(alias="thoughtsPending")
    thoughts_hidden: int = Field(alias="thoughtsHidden")
    thoughts_rejected: int = Field(alias="thoughtsRejected")
    thoughts_reported: int = Field(alias="thoughtsReported")
    gentle_words_submitted: int = Field(alias="gentleWordsSubmitted")
    gentle_words_approved: int = Field(alias="gentleWordsApproved")
    gentle_words_rejected: int = Field(alias="gentleWordsRejected")
    gentle_words_pending: int = Field(alias="gentleWordsPending")
    gentle_words_approval_rate_pct: float = Field(alias="gentleWordsApprovalRatePct")
    gentle_words_rejection_rate_pct: float = Field(alias="gentleWordsRejectionRatePct")
    median_moderation_seconds: Optional[float] = Field(default=None, alias="medianModerationSeconds")
    points_awarded_total: int = Field(alias="pointsAwardedTotal")
    points_redeemed_total: int = Field(alias="pointsRedeemedTotal")
    points_reversed_total: int = Field(alias="pointsReversedTotal")
    points_outstanding_total: int = Field(alias="pointsOutstandingTotal")
    perk_redemptions_total: int = Field(alias="perkRedemptionsTotal")
    perk_redemptions_used: int = Field(alias="perkRedemptionsUsed")
    perk_checkout_conversion_pct: float = Field(alias="perkCheckoutConversionPct")
    reports_pending: int = Field(alias="reportsPending")
    reports_per_100_posts: float = Field(alias="reportsPer100Posts")
