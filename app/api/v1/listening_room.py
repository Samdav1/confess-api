from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.sessions import get_session
from app.dependencies.auth import get_current_user
from app.models.listening_room import ReportTargetType, ThoughtCategory
from app.models.user import User
from app.schemas.listening_room import (
    AcknowledgementCreateRequest,
    AcknowledgementResponseItem,
    ApplyRedemptionRequest,
    ApplyRedemptionResponse,
    ConsumeOrReleaseRedemptionRequest,
    ContentReportCreateRequest,
    ContentReportResponseItem,
    DataResponse,
    GentleWordCreateItem,
    GentleWordCreateRequest,
    KindnessPointsBalanceData,
    LittlePerkPublicItem,
    OwnGentleWordsResponse,
    OwnThoughtItem,
    OwnThoughtsResponse,
    PerkRedemptionItem,
    PublicGentleWordItem,
    PublicGentleWordsListResponse,
    PublicThoughtDetailItem,
    RedeemPerkRequest,
    ThoughtCreateRequest,
    ThoughtFeedResponse,
    ThoughtLikeToggleResponse,
    ThoughtReportCreateRequest,
)
from app.service.listening_room_service import listening_room_service

router = APIRouter()
root_router = APIRouter()


# =========================================================================
# /api/v1/listening-room/thoughts/* AND /api/v1/thoughts/*
# =========================================================================


@router.get("/thoughts", response_model=ThoughtFeedResponse, summary="Browse The Listening Room feed")
@root_router.get("/thoughts", response_model=ThoughtFeedResponse, summary="Browse The Listening Room feed")
async def list_thoughts(
    category: Optional[ThoughtCategory] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    page: Optional[int] = Query(default=None, ge=1),
    page_size: Optional[int] = Query(default=None, ge=1, le=50, alias="pageSize"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    eff_limit = page_size or limit
    items, total, next_cursor = await listening_room_service.list_published_thoughts_feed(
        session=session,
        user_id=current_user.id,
        category=category.value if category else None,
        cursor=cursor,
        limit=eff_limit,
        page=page,
        page_size=page_size,
    )
    return ThoughtFeedResponse(
        data=items,
        items=items,
        total=total,
        page=page or 1,
        page_size=eff_limit,
        next_cursor=next_cursor,
    )


@router.post(
    "/thoughts",
    response_model=DataResponse[OwnThoughtItem],
    status_code=status.HTTP_201_CREATED,
    summary="Share a Thought in The Listening Room",
)
@root_router.post(
    "/thoughts",
    response_model=DataResponse[OwnThoughtItem],
    status_code=status.HTTP_201_CREATED,
    summary="Share a Thought in The Listening Room",
)
async def create_thought(
    request: ThoughtCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    thought = await listening_room_service.create_thought(
        session=session,
        user_id=current_user.id,
        body=request.body,
        category=request.category.value if request.category else None,
        author_label=request.author_label or "A quiet heart",
        tone=request.tone.value if request.tone else "cream",
        background_tasks=background_tasks,
    )
    return DataResponse(data=OwnThoughtItem(**thought))


@router.get(
    "/thoughts/{thought_id}",
    response_model=DataResponse[PublicThoughtDetailItem],
    summary="Get a published Thought and approved Gentle Words",
)
@root_router.get(
    "/thoughts/{thought_id}",
    response_model=DataResponse[PublicThoughtDetailItem],
    summary="Get a published Thought and approved Gentle Words",
)
async def get_thought_detail(
    thought_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    detail = await listening_room_service.get_thought_detail(
        session=session, thought_id=thought_id, user_id=current_user.id
    )
    return DataResponse(data=PublicThoughtDetailItem(**detail))


@router.delete(
    "/thoughts/{thought_id}",
    response_model=DataResponse[dict],
    summary="Delete your own Thought",
)
@root_router.delete(
    "/thoughts/{thought_id}",
    response_model=DataResponse[dict],
    summary="Delete your own Thought",
)
async def delete_thought(
    thought_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await listening_room_service.delete_my_thought(
        session=session, user_id=current_user.id, thought_id=thought_id
    )
    return DataResponse(data=result)


@router.post(
    "/thoughts/{thought_id}/likes",
    response_model=DataResponse[ThoughtLikeToggleResponse],
    summary="Toggle like on a published Thought",
)
@root_router.post(
    "/thoughts/{thought_id}/likes",
    response_model=DataResponse[ThoughtLikeToggleResponse],
    summary="Toggle like on a published Thought",
)
async def toggle_thought_like(
    thought_id: UUID,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    res = await listening_room_service.toggle_thought_like(
        session=session, user_id=current_user.id, thought_id=thought_id
    )
    return DataResponse(data=ThoughtLikeToggleResponse(**res))


@router.get(
    "/thoughts/{thought_id}/gentle-words",
    response_model=PublicGentleWordsListResponse,
    summary="List approved Gentle Words for a published Thought",
)
@root_router.get(
    "/thoughts/{thought_id}/gentle-words",
    response_model=PublicGentleWordsListResponse,
    summary="List approved Gentle Words for a published Thought",
)
async def list_thought_gentle_words(
    thought_id: UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50, alias="pageSize"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    items, total = await listening_room_service.list_approved_gentle_words(
        session=session, thought_id=thought_id, page=page, page_size=page_size
    )
    parsed = [PublicGentleWordItem(**item) for item in items]
    return PublicGentleWordsListResponse(
        data=parsed,
        items=parsed,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/thoughts/{thought_id}/gentle-words",
    response_model=DataResponse[GentleWordCreateItem],
    status_code=status.HTTP_201_CREATED,
    summary="Leave A Gentle Word on a Thought (enters Care Desk moderation)",
)
@root_router.post(
    "/thoughts/{thought_id}/gentle-words",
    response_model=DataResponse[GentleWordCreateItem],
    status_code=status.HTTP_201_CREATED,
    summary="Leave A Gentle Word on a Thought (enters Care Desk moderation)",
)
async def submit_gentle_word(
    thought_id: UUID,
    request: GentleWordCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    gw = await listening_room_service.submit_gentle_word(
        session=session,
        user_id=current_user.id,
        thought_id=thought_id,
        body=request.body,
        idempotency_key=request.idempotency_key,
        author_label=request.author_label or "A kind stranger",
        background_tasks=background_tasks,
    )
    return DataResponse(data=GentleWordCreateItem(**gw))


@router.post(
    "/thoughts/{thought_id}/acknowledgements",
    response_model=DataResponse[AcknowledgementResponseItem],
    summary="Toggle a supportive acknowledgement on a Thought",
)
@root_router.post(
    "/thoughts/{thought_id}/acknowledgements",
    response_model=DataResponse[AcknowledgementResponseItem],
    summary="Toggle a supportive acknowledgement on a Thought",
)
async def add_acknowledgement(
    thought_id: UUID,
    request: AcknowledgementCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    res = await listening_room_service.toggle_acknowledgement(
        session=session,
        user_id=current_user.id,
        thought_id=thought_id,
        ack_type=request.type,
    )
    return DataResponse(data=AcknowledgementResponseItem(**res))


@router.post(
    "/thoughts/{thought_id}/reports",
    response_model=DataResponse[ContentReportResponseItem],
    status_code=status.HTTP_201_CREATED,
    summary="Report a Thought",
)
@root_router.post(
    "/thoughts/{thought_id}/reports",
    response_model=DataResponse[ContentReportResponseItem],
    status_code=status.HTTP_201_CREATED,
    summary="Report a Thought",
)
async def report_thought(
    thought_id: UUID,
    request: ThoughtReportCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    report = await listening_room_service.create_report(
        session=session,
        user_id=current_user.id,
        target_type=ReportTargetType.THOUGHT,
        target_id=thought_id,
        reason=request.reason.value,
        details=request.details,
    )
    return DataResponse(data=ContentReportResponseItem(**report))


# =========================================================================
# /api/v1/me/*, /api/v1/user/*, /content-reports, /little-perks/*, /redemptions/*
# =========================================================================


@root_router.get("/me/thoughts", response_model=OwnThoughtsResponse, summary="Get my Thoughts and moderation status")
@root_router.get("/user/thoughts", response_model=OwnThoughtsResponse, summary="Get my Thoughts for profile tab")
async def get_my_thoughts(
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    items, next_cursor = await listening_room_service.list_my_thoughts(
        session=session, user_id=current_user.id, cursor=cursor, limit=limit
    )
    return OwnThoughtsResponse(data=items, items=items, next_cursor=next_cursor)


@root_router.get(
    "/me/gentle-words",
    response_model=OwnGentleWordsResponse,
    summary="Get my Gentle Word submissions and statuses",
)
@root_router.get(
    "/user/gentle-words",
    response_model=OwnGentleWordsResponse,
    summary="Get my Gentle Word submissions and statuses",
)
async def get_my_gentle_words(
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    items, next_cursor = await listening_room_service.list_my_gentle_words(
        session=session, user_id=current_user.id, cursor=cursor, limit=limit
    )
    return OwnGentleWordsResponse(data=items, items=items, next_cursor=next_cursor)


@root_router.post(
    "/content-reports",
    response_model=DataResponse[ContentReportResponseItem],
    status_code=status.HTTP_201_CREATED,
    summary="Report a Thought or Gentle Word",
)
async def create_content_report(
    request: ContentReportCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    report = await listening_room_service.create_report(
        session=session,
        user_id=current_user.id,
        target_type=request.target_type,
        target_id=request.target_id,
        reason=request.reason.value,
        details=request.details,
    )
    return DataResponse(data=ContentReportResponseItem(**report))


@root_router.get(
    "/me/kindness-points",
    response_model=DataResponse[KindnessPointsBalanceData],
    summary="Get Kindness Points balance and ledger history",
)
async def get_my_kindness_points(
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    data = await listening_room_service.get_my_kindness_points(
        session=session, user_id=current_user.id, cursor=cursor, limit=limit
    )
    return DataResponse(data=KindnessPointsBalanceData(**data))


@root_router.get(
    "/little-perks",
    response_model=DataResponse[List[LittlePerkPublicItem]],
    summary="List active Little Perk offers",
)
async def list_little_perks(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    perks = await listening_room_service.list_active_perks(session=session)
    return DataResponse(data=[LittlePerkPublicItem.model_validate(p) for p in perks])


@root_router.post(
    "/little-perks/{perk_id}/redeem",
    response_model=DataResponse[PerkRedemptionItem],
    summary="Redeem Kindness Points for a Little Perk",
)
async def redeem_little_perk(
    perk_id: UUID,
    request: RedeemPerkRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    redemption = await listening_room_service.redeem_perk(
        session=session,
        user_id=current_user.id,
        perk_id=perk_id,
        idempotency_key=request.idempotency_key,
        background_tasks=background_tasks,
    )
    return DataResponse(data=PerkRedemptionItem(**redemption))


@root_router.get(
    "/me/redemptions",
    response_model=DataResponse[List[PerkRedemptionItem]],
    summary="List my Little Perk redemptions",
)
async def list_my_redemptions(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    items = await listening_room_service.list_my_redemptions(session=session, user_id=current_user.id)
    return DataResponse(data=[PerkRedemptionItem(**item) for item in items])


@root_router.post(
    "/redemptions/{code}/apply",
    response_model=DataResponse[ApplyRedemptionResponse],
    summary="Validate and reserve a Little Perk redemption for a celebration checkout",
)
async def apply_redemption(
    code: str,
    request: ApplyRedemptionRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    applied = await listening_room_service.apply_redemption_at_checkout(
        session=session,
        user_id=current_user.id,
        code=code,
        celebration_id=request.celebration_id,
    )
    return DataResponse(data=ApplyRedemptionResponse(**applied))


@root_router.post(
    "/redemptions/{redemption_id}/consume",
    response_model=DataResponse[PerkRedemptionItem],
    summary="Mark a reserved Little Perk redemption as used (idempotent)",
)
async def consume_redemption(
    redemption_id: UUID,
    request: Optional[ConsumeOrReleaseRedemptionRequest] = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    from app.models.listening_room import PerkRedemption

    existing = await session.get(PerkRedemption, redemption_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Redemption not found.")
    if existing.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized.")

    redemption = await listening_room_service.consume_redemption(
        session=session,
        redemption_id=redemption_id,
        payment_reference=request.payment_reference if request else None,
        auto_commit=True,
    )
    return DataResponse(data=PerkRedemptionItem(**listening_room_service._serialize_redemption(redemption)))


@root_router.post(
    "/redemptions/{redemption_id}/release",
    response_model=DataResponse[PerkRedemptionItem],
    summary="Release a reserved Little Perk redemption back to available (idempotent)",
)
async def release_redemption(
    redemption_id: UUID,
    request: Optional[ConsumeOrReleaseRedemptionRequest] = None,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    from app.models.listening_room import PerkRedemption

    existing = await session.get(PerkRedemption, redemption_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Redemption not found.")
    if existing.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized.")

    redemption = await listening_room_service.release_redemption(
        session=session,
        redemption_id=redemption_id,
        auto_commit=True,
    )
    return DataResponse(data=PerkRedemptionItem(**listening_room_service._serialize_redemption(redemption)))
