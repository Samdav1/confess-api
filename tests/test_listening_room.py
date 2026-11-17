import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.admin import Admin
from app.models.celebration import CelebrationPage, DeliveryMethod, MusicType, OccasionType, PaymentStatus
from app.models.listening_room import (
    DiscountType,
    GentleWordStatus,
    KindnessPointsLedger,
    KindnessPointsPolicy,
    LittlePerk,
    RedemptionStatus,
    ThoughtCategory,
    ThoughtStatus,
    UserKindnessBalance,
)
from app.models.user import User
from app.schemas.listening_room import (
    GentleWordCreateRequest,
    OwnGentleWordItem,
    OwnThoughtItem,
    PublicGentleWordItem,
    PublicThoughtDetailItem,
    PublicThoughtItem,
    ThoughtCreateRequest,
)
from app.service.listening_room_service import (
    ListeningRoomService,
    _CRISIS_PATTERNS,
    _decode_cursor,
    _encode_cursor,
    _sanitize_text,
)


def test_privacy_safe_serialization():
    now = datetime.now(timezone.utc)
    thought_id = uuid4()
    gw_id = uuid4()

    public_gw = PublicGentleWordItem(
        id=gw_id,
        thought_id=thought_id,
        body="You are not alone.",
        created_at=now,
        approved_at=now,
        author_user_id=uuid4(),
        moderation_note="internal staff note",
        moderated_by=uuid4(),
    )
    gw_dump = public_gw.model_dump(by_alias=True)
    for forbidden in ("author_user_id", "authorUserId", "moderation_note", "moderationNote", "moderated_by"):
        assert forbidden not in gw_dump

    public_thought = PublicThoughtDetailItem(
        id=thought_id,
        body="Feeling overwhelmed lately.",
        category="personal",
        created_at=now,
        published_at=now,
        gentle_words_count=1,
        acknowledgements=["im_listening"],
        gentle_words=[public_gw],
        author_user_id=uuid4(),
        moderation_note="secret note",
    )
    t_dump = public_thought.model_dump(by_alias=True)
    for forbidden in ("author_user_id", "authorUserId", "moderation_note", "moderationNote", "moderated_by"):
        assert forbidden not in t_dump

    own_thought = OwnThoughtItem(
        id=thought_id,
        body="Feeling overwhelmed lately.",
        category="personal",
        status="pending",
        moderation_status="pending",
        gentle_words_count=0,
        created_at=now,
        moderation_note="secret note",
    )
    assert "moderationNote" not in own_thought.model_dump(by_alias=True)

    own_gw = OwnGentleWordItem(
        id=gw_id,
        thought_id=thought_id,
        body="Stay strong.",
        status="rejected",
        points_awarded=0,
        created_at=now,
        moderation_note="internal rejection details",
    )
    assert "moderationNote" not in own_gw.model_dump(by_alias=True)


def test_input_sanitization_and_crisis_detection():
    cleaned = _sanitize_text("  <b>Hello</b> world\x00  ")
    assert cleaned == "Hello world"

    assert _CRISIS_PATTERNS.search("I feel like I want to end my life") is not None
    assert _CRISIS_PATTERNS.search("Having a tough day at work") is None

    with pytest.raises(ValueError):
        ThoughtCreateRequest(body="   short   ", acceptGuidelines=True)

    with pytest.raises(ValueError):
        ThoughtCreateRequest(body="This is a valid thought message", acceptGuidelines=False)

    req = GentleWordCreateRequest(body="  Sending warmth to you!  ")
    assert req.body == "Sending warmth to you!"


def test_discount_math_and_cursors():
    svc = ListeningRoomService()

    pct_perk = LittlePerk(
        name="10% Off",
        description="10% off capped at 500",
        points_cost=100,
        discount_type=DiscountType.PERCENTAGE.value,
        discount_value=10.0,
        maximum_discount_amount=500.0,
    )
    assert svc._compute_discount(pct_perk, 2000.0) == 200.0
    assert svc._compute_discount(pct_perk, 10000.0) == 500.0  # capped

    fixed_perk = LittlePerk(
        name="500 Off",
        description="500 NGN off",
        points_cost=200,
        discount_type=DiscountType.FIXED_AMOUNT.value,
        discount_value=500.0,
    )
    assert svc._compute_discount(fixed_perk, 1000.0) == 500.0
    assert svc._compute_discount(fixed_perk, 300.0) == 300.0  # never exceeds order amount

    ts = datetime.now(timezone.utc)
    uid = uuid4()
    cur = _encode_cursor(ts, uid)
    decoded_ts, decoded_id = _decode_cursor(cur)
    assert decoded_ts == ts
    assert decoded_id == uid

    with pytest.raises(HTTPException):
        _decode_cursor("invalid-cursor-token")


@pytest.mark.asyncio
async def test_listening_room_end_to_end_workflow():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    svc = ListeningRoomService()

    async with async_session() as session:
        author = User(username="author", email="author@example.com", password="pw", referral_code="REF1")
        helper = User(username="helper", email="helper@example.com", password="pw", referral_code="REF2")
        admin = Admin(username="mod", email="mod@example.com", password="pw", is_super_admin=True)
        session.add_all([author, helper, admin])
        await session.flush()

        policy = KindnessPointsPolicy(
            id=1,
            points_per_gentle_word=50,
            minimum_points_to_redeem=50,
            daily_award_cap=100,
            weekly_award_cap=200,
            require_thought_moderation=True,
        )
        perk = LittlePerk(
            name="10% Off Celebration",
            description="10% off any celebration",
            points_cost=50,
            discount_type=DiscountType.PERCENTAGE.value,
            discount_value=10.0,
            currency="NGN",
            eligible_celebration_types=[],
            minimum_order_amount=500.0,
            maximum_discount_amount=500.0,
            active=True,
        )
        celebration = CelebrationPage(
            slug="happy-day-test",
            recipient_name="Friend",
            occasion_type=OccasionType.BIRTHDAY,
            images=[],
            music_type=MusicType.NONE,
            delivery=DeliveryMethod.EMAIL,
            email="friend@example.com",
            created_by=helper.id,
            total_price=1000.0,
            payment_status=PaymentStatus.PENDING,
        )
        session.add_all([policy, perk, celebration])
        await session.commit()

        # 1. Create Thought -> enters pending moderation
        created_t = await svc.create_thought(
            session=session,
            user_id=author.id,
            body="I have been carrying a lot on my mind this week.",
            category=ThoughtCategory.PERSONAL.value,
        )
        assert created_t["status"] == ThoughtStatus.PENDING.value

        # Pending Thought must NOT appear in public feed
        feed, _ = await svc.list_published_thoughts(session)
        assert len(feed) == 0

        # 2. Care Desk approves Thought -> appears in feed
        await svc.approve_thought(session, admin.id, created_t["id"], note="Safe and authentic")
        feed, _ = await svc.list_published_thoughts(session)
        assert len(feed) == 1
        assert feed[0]["id"] == created_t["id"]

        # 3. Helper leaves A Gentle Word -> pending (not visible in public detail)
        gw = await svc.submit_gentle_word(
            session=session,
            user_id=helper.id,
            thought_id=created_t["id"],
            body="You are doing your best, and it is okay to take one step at a time.",
            idempotency_key="gw-req-1",
        )
        assert gw["status"] == GentleWordStatus.PENDING.value
        detail = await svc.get_thought_detail(session, created_t["id"])
        assert len(detail["gentle_words"]) == 0

        # Self-reply earns 0 points
        self_gw = await svc.submit_gentle_word(
            session=session,
            user_id=author.id,
            thought_id=created_t["id"],
            body="Thank you to anyone reading this today.",
            idempotency_key="gw-req-self",
        )
        approved_self = await svc.approve_gentle_word(session, admin.id, self_gw["id"])
        assert approved_self.points_awarded == 0

        # 4. Approve helper's Gentle Word -> visible + awards 50 points exactly once
        approved_gw = await svc.approve_gentle_word(session, admin.id, gw["id"], note="Warm and safe")
        assert approved_gw.points_awarded == 50

        # Idempotent repeat approval does NOT duplicate points
        approved_gw_repeat = await svc.approve_gentle_word(session, admin.id, gw["id"])
        assert approved_gw_repeat.points_awarded == 50

        pts_data = await svc.get_my_kindness_points(session, helper.id)
        assert pts_data["balance"] == 50

        # 5. Redeem Little Perk (with idempotency)
        red = await svc.redeem_perk(session, helper.id, perk.id, idempotency_key="redeem-1")
        red_dup = await svc.redeem_perk(session, helper.id, perk.id, idempotency_key="redeem-1")
        assert red["id"] == red_dup["id"]
        assert red["code"] == red_dup["code"]

        pts_after = await svc.get_my_kindness_points(session, helper.id)
        assert pts_after["balance"] == 0

        # Insufficient balance rejected
        with pytest.raises(HTTPException) as exc:
            await svc.redeem_perk(session, helper.id, perk.id, idempotency_key="redeem-2")
        assert exc.value.status_code == 400

        # 6. Checkout apply -> reserve -> release -> re-apply -> consume -> refund
        applied = await svc.apply_redemption_at_checkout(
            session=session,
            user_id=helper.id,
            code=red["code"],
            celebration_id=celebration.id,
        )
        assert applied["status"] == RedemptionStatus.RESERVED.value
        assert applied["discount_amount"] == 100.0
        assert applied["final_amount"] == 900.0

        released = await svc.release_redemption(session, applied["redemption_id"])
        assert released.status == RedemptionStatus.AVAILABLE.value

        applied_again = await svc.apply_redemption_at_checkout(
            session=session,
            user_id=helper.id,
            code=red["code"],
            celebration_id=celebration.id,
        )
        consumed = await svc.consume_redemption(session, applied_again["redemption_id"], payment_reference="PAY-REF-1")
        assert consumed.status == RedemptionStatus.USED.value

        # Idempotent consume
        consumed_again = await svc.consume_redemption(session, applied_again["redemption_id"])
        assert consumed_again.status == RedemptionStatus.USED.value

        # Refund restores points
        refunded = await svc.refund_redemption_on_payment_reversal(session, applied_again["redemption_id"])
        assert refunded.status == RedemptionStatus.REFUNDED.value
        pts_refunded = await svc.get_my_kindness_points(session, helper.id)
        assert pts_refunded["balance"] == 50

    await engine.dispose()
