from fastapi import APIRouter, Depends, Query
from sqlmodel.ext.asyncio.session import AsyncSession
from typing import Dict, Any, Optional
from uuid import UUID

from app.db.sessions import get_session
from app.dependencies.auth import get_current_admin
from app.schemas.auth import LoginResponse
from app.schemas.admin import (
    AdminCreate,
    AdminLogin,
    AdminResponse,
    AdminPasswordReset,
    AdminLoginResponse,
    DashboardStatsResponse,
    FinancialAnalysisResponse,
    AdminPaymentStatus,
    AdminPaymentUpdateRequest,
    AdminPaymentRecordResponse,
    PaginatedPaymentsResponse,
    PaginatedUsersResponse,
    PaginatedConfessFormsResponse,
    PaginatedAnonymousLinksResponse,
    PaginatedAnonymousMessagesResponse
)
from app.service.admin import (
    create_admin,
    login_admin,
    reset_admin_password,
    get_dashboard_metrics,
    get_financial_analysis_admin,
    get_all_payments_admin,
    update_payment_record_admin,
    get_all_users_admin,
    get_all_confess_forms_admin,
    get_all_anonymous_links_admin,
    get_all_anonymous_messages_admin
)
from app.models.admin import Admin

router = APIRouter()

@router.post("/create", response_model=AdminResponse, summary="Create a new admin account")
async def create_new_admin(
    request: AdminCreate,
    db: AsyncSession = Depends(get_session)
):
    admin = await create_admin(db=db, admin_in=request)
    return admin

@router.post("/login", response_model=AdminLoginResponse, summary="Login admin account")
async def login(
    request: AdminLogin,
    db: AsyncSession = Depends(get_session)
):
    admin, access_token, expires_in = await login_admin(
        db=db,
        email=request.email,
        password=request.password
    )
    return AdminLoginResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
        admin=AdminResponse.model_validate(admin)
    )

@router.post("/reset-password", response_model=AdminResponse, summary="Reset admin password")
async def reset_password(
    request: AdminPasswordReset,
    db: AsyncSession = Depends(get_session)
):
    return await reset_admin_password(
        db=db,
        email=request.email,
        new_password=request.new_password
    )

@router.get("/dashboard-stats", response_model=DashboardStatsResponse, summary="[Admin] Get Dashboard Summary")
async def dashboard_stats(
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session)
):
    """
    Get backend summary totals strictly for Admin dashboard users.
    """
    metrics = await get_dashboard_metrics(db=db)
    return DashboardStatsResponse(**metrics)

@router.get("/financial-analysis", response_model=FinancialAnalysisResponse, summary="[Admin] Get Comprehensive Financial Analysis")
async def financial_analysis(
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session)
):
    """
    Get full financial analytics: total orders, successful and pending transactions,
    revenue breakdown by service, success rates, averages, and recent transaction data.
    """
    data = await get_financial_analysis_admin(db=db)
    return FinancialAnalysisResponse(**data)

@router.get("/financial-records", response_model=PaginatedPaymentsResponse, summary="[Admin] List financial records by status")
async def list_financial_records(
    status: Optional[AdminPaymentStatus] = Query(default=None, description="Filter by payment status"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session)
):
    """List paginated financial (payment) records with optional status filter."""
    return await get_all_payments_admin(
        db=db,
        skip=skip,
        limit=limit,
        payment_status=status.value if status else None,
    )

@router.patch("/financial-records/{payment_id}", response_model=AdminPaymentRecordResponse, summary="[Admin] Update financial record status")
@router.put("/financial-records/{payment_id}", response_model=AdminPaymentRecordResponse, summary="[Admin] Update financial record status")
async def update_financial_record(
    payment_id: UUID,
    request: AdminPaymentUpdateRequest,
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session)
):
    """
    Update a financial record's status (pending, success, failed, abandoned, reversed, refunded)
    and optionally amount, channel, or paid_at, with an audit log entry.
    """
    payment = await update_payment_record_admin(
        db=db,
        admin_id=current_admin.id,
        payment_id=payment_id,
        new_status=request.status.value,
        amount=request.amount,
        channel=request.channel,
        paid_at=request.paid_at,
        reason=request.reason,
    )
    return AdminPaymentRecordResponse.model_validate(payment)

@router.get("/users", response_model=PaginatedUsersResponse, summary="[Admin] Get paginated users")
async def get_users(
    skip: int = 0,
    limit: int = 100,
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session)
):
    """Get all standard users."""
    data = await get_all_users_admin(db=db, skip=skip, limit=limit)
    return data

@router.get("/confess-forms", response_model=PaginatedConfessFormsResponse, summary="[Admin] Get paginated confess forms")
async def get_confess_forms(
    skip: int = 0,
    limit: int = 100,
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session)
):
    """Get all confess forms."""
    data = await get_all_confess_forms_admin(db=db, skip=skip, limit=limit)
    return data

@router.get("/anonymous-links", response_model=PaginatedAnonymousLinksResponse, summary="[Admin] Get paginated anonymous links")
async def get_anonymous_links(
    skip: int = 0,
    limit: int = 100,
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session)
):
    """Get all anonymous links."""
    data = await get_all_anonymous_links_admin(db=db, skip=skip, limit=limit)
    return data

@router.get("/anonymous-messages", response_model=PaginatedAnonymousMessagesResponse, summary="[Admin] Get paginated anonymous messages")
async def get_anonymous_messages(
    skip: int = 0,
    limit: int = 100,
    current_admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session)
):
    """Get all anonymous messages."""
    data = await get_all_anonymous_messages_admin(db=db, skip=skip, limit=limit)
    return data


from app.api.v1.admin_care_desk import router as care_desk_router

router.include_router(care_desk_router, tags=["Care Desk"])
