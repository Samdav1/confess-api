from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Query
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.sessions import get_session
from app.dependencies.auth import get_current_admin
from app.models.admin import Admin
from app.models.listening_room import (
    GentleWordStatus,
    LittlePerk,
    ReportStatus,
    ReportTargetType,
    ThoughtStatus,
)
from app.schemas.listening_room import (
    AdminGentleWordQueueItem,
    AdminQueuePaginatedResponse,
    AdminReportItem,
    AdminThoughtQueueItem,
    DataResponse,
    HideContentRequest,
    KindnessPointsPolicyResponse,
    KindnessPointsPolicyUpdateRequest,
    LedgerEntryItem,
    ListeningRoomAnalyticsResponse,
    LittlePerkCreateRequest,
    LittlePerkPublicItem,
    LittlePerkUpdateRequest,
    ModerationDecisionRequest,
    PointsAdjustRequest,
    PostingRestrictionRequest,
    PostingRestrictionResponse,
    ResolveReportRequest,
)
from app.service.listening_room_service import listening_room_service

router = APIRouter()


@router.get(
    "/care-desk/thoughts",
    response_model=AdminQueuePaginatedResponse[AdminThoughtQueueItem],
    summary="[Care Desk] List Thoughts moderation queue",
)
async def list_care_desk_thoughts(
    status: Optional[ThoughtStatus] = Query(default=ThoughtStatus.PENDING),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    data = await listening_room_service.list_care_desk_thoughts(
        session=db,
        thought_status=status.value if status else None,
        skip=skip,
        limit=limit,
    )
    return AdminQueuePaginatedResponse(
        total=data["total"],
        items=[AdminThoughtQueueItem.model_validate(item) for item in data["items"]],
    )


@router.get(
    "/care-desk/gentle-words",
    response_model=AdminQueuePaginatedResponse[AdminGentleWordQueueItem],
    summary="[Care Desk] List Gentle Words moderation queue",
)
async def list_care_desk_gentle_words(
    status: Optional[GentleWordStatus] = Query(default=GentleWordStatus.PENDING_REVIEW),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    data = await listening_room_service.list_care_desk_gentle_words(
        session=db,
        gw_status=status.value if status else None,
        skip=skip,
        limit=limit,
    )
    return AdminQueuePaginatedResponse(
        total=data["total"],
        items=[AdminGentleWordQueueItem(**item) for item in data["items"]],
    )


@router.post(
    "/thoughts/{thought_id}/approve",
    response_model=DataResponse[AdminThoughtQueueItem],
    summary="[Care Desk] Approve and publish a Thought",
)
@router.patch(
    "/care-desk/thoughts/{thought_id}/approve",
    response_model=DataResponse[AdminThoughtQueueItem],
    summary="[Care Desk] Approve and publish a Thought",
)
@router.post(
    "/care-desk/thoughts/{thought_id}/approve",
    response_model=DataResponse[AdminThoughtQueueItem],
    summary="[Care Desk] Approve and publish a Thought",
)
async def approve_thought(
    thought_id: UUID,
    background_tasks: BackgroundTasks,
    request: Optional[ModerationDecisionRequest] = Body(default=None),
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    thought = await listening_room_service.approve_thought(
        session=db,
        admin_id=current_admin.id,
        thought_id=thought_id,
        note=request.note if request else None,
        background_tasks=background_tasks,
    )
    return DataResponse(data=AdminThoughtQueueItem.model_validate(thought))


@router.post(
    "/thoughts/{thought_id}/reject",
    response_model=DataResponse[AdminThoughtQueueItem],
    summary="[Care Desk] Reject a Thought",
)
@router.patch(
    "/care-desk/thoughts/{thought_id}/reject",
    response_model=DataResponse[AdminThoughtQueueItem],
    summary="[Care Desk] Reject a Thought",
)
@router.post(
    "/care-desk/thoughts/{thought_id}/reject",
    response_model=DataResponse[AdminThoughtQueueItem],
    summary="[Care Desk] Reject a Thought",
)
async def reject_thought(
    thought_id: UUID,
    background_tasks: BackgroundTasks,
    request: Optional[ModerationDecisionRequest] = Body(default=None),
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    thought = await listening_room_service.reject_thought(
        session=db,
        admin_id=current_admin.id,
        thought_id=thought_id,
        reason=request.reason if request else None,
        note=request.note if request else None,
        background_tasks=background_tasks,
    )
    return DataResponse(data=AdminThoughtQueueItem.model_validate(thought))


@router.post(
    "/gentle-words/{gentle_word_id}/approve",
    response_model=DataResponse[AdminGentleWordQueueItem],
    summary="[Care Desk] Approve Gentle Word and award Kindness Points atomically",
)
@router.patch(
    "/care-desk/gentle-words/{gentle_word_id}/approve",
    response_model=DataResponse[AdminGentleWordQueueItem],
    summary="[Care Desk] Approve Gentle Word and award Kindness Points atomically",
)
@router.post(
    "/care-desk/gentle-words/{gentle_word_id}/approve",
    response_model=DataResponse[AdminGentleWordQueueItem],
    summary="[Care Desk] Approve Gentle Word and award Kindness Points atomically",
)
async def approve_gentle_word(
    gentle_word_id: UUID,
    background_tasks: BackgroundTasks,
    request: Optional[ModerationDecisionRequest] = Body(default=None),
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    gw = await listening_room_service.approve_gentle_word(
        session=db,
        admin_id=current_admin.id,
        gentle_word_id=gentle_word_id,
        note=request.note if request else None,
        background_tasks=background_tasks,
    )
    return DataResponse(data=AdminGentleWordQueueItem.model_validate(gw))


@router.post(
    "/gentle-words/{gentle_word_id}/reject",
    response_model=DataResponse[AdminGentleWordQueueItem],
    summary="[Care Desk] Reject Gentle Word (awards 0 points)",
)
@router.patch(
    "/care-desk/gentle-words/{gentle_word_id}/reject",
    response_model=DataResponse[AdminGentleWordQueueItem],
    summary="[Care Desk] Reject Gentle Word (awards 0 points)",
)
@router.post(
    "/care-desk/gentle-words/{gentle_word_id}/reject",
    response_model=DataResponse[AdminGentleWordQueueItem],
    summary="[Care Desk] Reject Gentle Word (awards 0 points)",
)
async def reject_gentle_word(
    gentle_word_id: UUID,
    background_tasks: BackgroundTasks,
    request: Optional[ModerationDecisionRequest] = Body(default=None),
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    gw = await listening_room_service.reject_gentle_word(
        session=db,
        admin_id=current_admin.id,
        gentle_word_id=gentle_word_id,
        reason=request.reason if request else None,
        note=request.note if request else None,
        background_tasks=background_tasks,
    )
    return DataResponse(data=AdminGentleWordQueueItem.model_validate(gw))


@router.post(
    "/content/{target_type}/{target_id}/hide",
    response_model=DataResponse[dict],
    summary="[Care Desk] Hide published Thought or Gentle Word with audit entry",
)
async def hide_content(
    target_type: ReportTargetType,
    target_id: UUID,
    request: HideContentRequest,
    background_tasks: BackgroundTasks,
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    res = await listening_room_service.hide_content(
        session=db,
        admin_id=current_admin.id,
        target_type=target_type,
        target_id=target_id,
        reason=request.reason,
        reverse_points=request.reverse_points,
        background_tasks=background_tasks,
    )
    return DataResponse(data=res)


@router.get(
    "/reports",
    response_model=AdminQueuePaginatedResponse[AdminReportItem],
    summary="[Care Desk] List Content Reports queue",
)
async def list_reports(
    status: Optional[ReportStatus] = Query(default=ReportStatus.PENDING),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    data = await listening_room_service.list_reports(
        session=db,
        report_status=status.value if status else None,
        skip=skip,
        limit=limit,
    )
    return AdminQueuePaginatedResponse(
        total=data["total"],
        items=[AdminReportItem(**item) for item in data["items"]],
    )


@router.post(
    "/reports/{report_id}/resolve",
    response_model=DataResponse[AdminReportItem],
    summary="[Care Desk] Resolve or dismiss a Content Report",
)
async def resolve_report(
    report_id: UUID,
    request: ResolveReportRequest,
    background_tasks: BackgroundTasks,
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    report = await listening_room_service.resolve_report(
        session=db,
        admin_id=current_admin.id,
        report_id=report_id,
        new_status=request.status,
        note=request.note,
        hide_target_content=request.hide_target_content,
        reverse_points=request.reverse_points,
        background_tasks=background_tasks,
    )
    return DataResponse(data=AdminReportItem.model_validate(report))


@router.post(
    "/users/{user_id}/posting-restrictions",
    response_model=DataResponse[PostingRestrictionResponse],
    summary="[Care Desk] Restrict or unrestrict a user's Listening Room posting privileges",
)
async def set_posting_restriction(
    user_id: UUID,
    request: PostingRestrictionRequest,
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    res = await listening_room_service.set_user_posting_restriction(
        session=db,
        admin_id=current_admin.id,
        user_id=user_id,
        restricted=request.restricted,
        reason=request.reason,
        duration_hours=request.duration_hours,
    )
    return DataResponse(data=PostingRestrictionResponse(**res))


@router.post(
    "/kindness-points/adjust",
    response_model=DataResponse[LedgerEntryItem],
    summary="[Care Desk] Manually adjust or reverse a member's Kindness Points with audit log",
)
async def adjust_kindness_points(
    request: PointsAdjustRequest,
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    entry = await listening_room_service.adjust_kindness_points(
        session=db,
        admin_id=current_admin.id,
        user_id=request.user_id,
        points_delta=request.points_delta,
        reason=request.reason,
        idempotency_key=request.idempotency_key,
    )
    return DataResponse(data=LedgerEntryItem(**entry))


@router.get(
    "/kindness-points/policy",
    response_model=DataResponse[KindnessPointsPolicyResponse],
    summary="[Admin] Get Kindness Points policy configuration",
)
async def get_points_policy(
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    policy = await listening_room_service.get_or_create_policy(session=db)
    return DataResponse(data=KindnessPointsPolicyResponse.model_validate(policy))


@router.patch(
    "/kindness-points/policy",
    response_model=DataResponse[KindnessPointsPolicyResponse],
    summary="[Admin] Update Kindness Points policy configuration",
)
async def update_points_policy(
    request: KindnessPointsPolicyUpdateRequest,
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    policy = await listening_room_service.update_points_policy(
        session=db,
        admin_id=current_admin.id,
        updates=request.model_dump(exclude_unset=True),
    )
    return DataResponse(data=KindnessPointsPolicyResponse.model_validate(policy))


@router.get(
    "/little-perks",
    response_model=DataResponse[List[LittlePerkPublicItem]],
    summary="[Admin] List all Little Perk offers",
)
async def list_all_little_perks(
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    perks = (await db.execute(select(LittlePerk).order_by(LittlePerk.created_at.desc()))).scalars().all()
    return DataResponse(data=[LittlePerkPublicItem.model_validate(p) for p in perks])


@router.post(
    "/little-perks",
    response_model=DataResponse[LittlePerkPublicItem],
    summary="[Admin] Create a Little Perk offer",
)
async def create_little_perk(
    request: LittlePerkCreateRequest,
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    perk = await listening_room_service.create_little_perk(
        session=db,
        admin_id=current_admin.id,
        data=request.model_dump(),
    )
    return DataResponse(data=LittlePerkPublicItem.model_validate(perk))


@router.patch(
    "/little-perks/{perk_id}",
    response_model=DataResponse[LittlePerkPublicItem],
    summary="[Admin] Update a Little Perk offer",
)
async def update_little_perk(
    perk_id: UUID,
    request: LittlePerkUpdateRequest,
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    perk = await listening_room_service.update_little_perk(
        session=db,
        admin_id=current_admin.id,
        perk_id=perk_id,
        updates=request.model_dump(exclude_unset=True),
    )
    return DataResponse(data=LittlePerkPublicItem.model_validate(perk))


@router.get(
    "/listening-room/analytics",
    response_model=DataResponse[ListeningRoomAnalyticsResponse],
    summary="[Admin] Get Listening Room product & safety analytics",
)
async def get_listening_room_analytics(
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    data = await listening_room_service.get_listening_room_analytics(session=db)
    return DataResponse(data=ListeningRoomAnalyticsResponse(**data))
