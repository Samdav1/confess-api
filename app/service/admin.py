from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, func, case, col
from fastapi import HTTPException, status
from typing import Tuple, Dict, Any, List
from datetime import datetime, timezone
import json

from app.models.admin import Admin
from app.models.user import User
from app.models.confess import AnonymousLink, AnonymousMessage
from app.models.confess_form import ConfessForm
from app.models.celebration import CelebrationPage, PaymentStatus
from app.models.payment import Payment
from app.schemas.admin import AdminCreate
from passlib.context import CryptContext
from app.dependencies.auth import create_access_token, get_token_expiry_seconds

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

async def create_admin(db: AsyncSession, admin_in: AdminCreate) -> Admin:
    statement = select(Admin).where(Admin.email == admin_in.email)
    result = await db.execute(statement)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin with this email already exists"
        )

    admin = Admin(
        email=admin_in.email,
        username=admin_in.username,
        password=get_password_hash(admin_in.password),
        is_super_admin=admin_in.is_super_admin
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    return admin

async def login_admin(db: AsyncSession, email: str, password: str) -> Tuple[Admin, str, int]:
    statement = select(Admin).where(Admin.email == email)
    result = await db.execute(statement)
    admin = result.scalar_one_or_none()

    if not admin or not verify_password(password, admin.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        user_id=str(admin.id),
        email=admin.email
    )
    expires_in = get_token_expiry_seconds()

    return admin, access_token, expires_in

async def reset_admin_password(db: AsyncSession, email: str, new_password: str) -> Admin:
    statement = select(Admin).where(Admin.email == email)
    result = await db.execute(statement)
    admin = result.scalar_one_or_none()

    if not admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin not found"
        )

    admin.password = get_password_hash(new_password)
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    return admin

async def get_dashboard_metrics(db: AsyncSession) -> Dict[str, int]:
    try:
        total_users = await db.execute(select(func.count()).select_from(User))
        total_confess_forms = await db.execute(select(func.count()).select_from(ConfessForm))
        total_anonymous_links = await db.execute(select(func.count()).select_from(AnonymousLink))
        total_messages = await db.execute(select(func.count()).select_from(AnonymousMessage))

        return {
            "total_users": total_users.scalar() or 0,
            "total_confess_forms": total_confess_forms.scalar() or 0,
            "total_anonymous_links": total_anonymous_links.scalar() or 0,
            "total_messages": total_messages.scalar() or 0
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch dashboard metrics: {str(e)}"
        )

async def get_all_users_admin(db: AsyncSession, skip: int = 0, limit: int = 100) -> Dict[str, Any]:
    total = await db.execute(select(func.count()).select_from(User))
    statement = select(User).order_by(User.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(statement)
    return {"total": total.scalar() or 0, "items": result.scalars().all()}

async def get_all_confess_forms_admin(db: AsyncSession, skip: int = 0, limit: int = 100) -> Dict[str, Any]:
    total = await db.execute(select(func.count()).select_from(ConfessForm))
    statement = select(ConfessForm).order_by(ConfessForm.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(statement)
    return {"total": total.scalar() or 0, "items": result.scalars().all()}

async def get_all_anonymous_links_admin(db: AsyncSession, skip: int = 0, limit: int = 100) -> Dict[str, Any]:
    total = await db.execute(select(func.count()).select_from(AnonymousLink))
    statement = select(AnonymousLink).order_by(AnonymousLink.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(statement)
    return {"total": total.scalar() or 0, "items": result.scalars().all()}

async def get_all_anonymous_messages_admin(db: AsyncSession, skip: int = 0, limit: int = 100) -> Dict[str, Any]:
    total = await db.execute(select(func.count()).select_from(AnonymousMessage))
    statement = select(AnonymousMessage).order_by(AnonymousMessage.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(statement)
    return {"total": total.scalar() or 0, "items": result.scalars().all()}

async def get_financial_analysis_admin(db: AsyncSession) -> Dict[str, Any]:
    """
    Compute comprehensive financial analytics across all orders,
    transactions, and revenue-generating services.
    """
    try:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # 1. Overall Payment metrics
        stmt_payments = select(
            func.count(Payment.id).label("total_orders"),
            func.coalesce(func.sum(case((Payment.status == "success", Payment.amount), else_=0.0)), 0.0).label("total_revenue"),
            func.count(case((Payment.status == "success", Payment.id))).label("successful_count"),
            func.count(case((Payment.status == "pending", Payment.id))).label("pending_count"),
            func.count(case((Payment.status == "failed", Payment.id))).label("failed_count"),
            func.count(case((Payment.status == "abandoned", Payment.id))).label("abandoned_count"),
            func.coalesce(func.sum(case((Payment.status == "success", case((Payment.created_at >= today_start, Payment.amount), else_=0.0)), else_=0.0)), 0.0).label("revenue_today"),
            func.coalesce(func.sum(case((Payment.status == "success", case((Payment.created_at >= month_start, Payment.amount), else_=0.0)), else_=0.0)), 0.0).label("revenue_month"),
        )
        res_payments = (await db.execute(stmt_payments)).first()

        total_orders = int(res_payments.total_orders or 0)
        total_revenue = float(res_payments.total_revenue or 0.0)
        successful_transactions = int(res_payments.successful_count or 0)
        pending_transactions = int(res_payments.pending_count or 0)
        failed_transactions = int(res_payments.failed_count or 0)
        abandoned_transactions = int(res_payments.abandoned_count or 0)
        revenue_today = float(res_payments.revenue_today or 0.0)
        revenue_this_month = float(res_payments.revenue_month or 0.0)

        success_rate = (
            round((successful_transactions / total_orders) * 100, 2)
            if total_orders > 0 else 0.0
        )
        average_order_value = (
            round(total_revenue / successful_transactions, 2)
            if successful_transactions > 0 else 0.0
        )

        # 2. Service-level breakdown: Confess Forms
        confess_stmt = select(
            func.count(ConfessForm.id).label("total_orders"),
            func.count(case((ConfessForm.paid == True, ConfessForm.id))).label("successful_count"),
            func.count(case((ConfessForm.paid == False, ConfessForm.id))).label("pending_count")
        )
        res_confess = (await db.execute(confess_stmt)).first()
        cf_total = int(res_confess.total_orders or 0)
        cf_success = int(res_confess.successful_count or 0)
        cf_pending = int(res_confess.pending_count or 0)
        # Confess form pricing standard is 350 NGN
        cf_revenue = round(cf_success * 350.0, 2)

        confess_service_data = {
            "service_name": "Confess Forms",
            "total_revenue": cf_revenue,
            "successful_count": cf_success,
            "pending_count": cf_pending,
            "failed_count": 0,
            "total_orders": cf_total
        }

        # 3. Service-level breakdown: Celebration Pages
        celeb_stmt = select(
            func.count(CelebrationPage.id).label("total_orders"),
            func.coalesce(func.sum(case((CelebrationPage.payment_status == PaymentStatus.PAID, CelebrationPage.total_price), else_=0.0)), 0.0).label("total_revenue"),
            func.count(case((CelebrationPage.payment_status == PaymentStatus.PAID, CelebrationPage.id))).label("successful_count"),
            func.count(case((CelebrationPage.payment_status == PaymentStatus.PENDING, CelebrationPage.id))).label("pending_count"),
            func.count(case((CelebrationPage.payment_status == PaymentStatus.FAILED, CelebrationPage.id))).label("failed_count")
        )
        res_celeb = (await db.execute(celeb_stmt)).first()
        celeb_total = int(res_celeb.total_orders or 0)
        celeb_revenue = float(res_celeb.total_revenue or 0.0)
        celeb_success = int(res_celeb.successful_count or 0)
        celeb_pending = int(res_celeb.pending_count or 0)
        celeb_failed = int(res_celeb.failed_count or 0)

        celebration_service_data = {
            "service_name": "Celebration Pages",
            "total_revenue": round(celeb_revenue, 2),
            "successful_count": celeb_success,
            "pending_count": celeb_pending,
            "failed_count": celeb_failed,
            "total_orders": celeb_total
        }

        # 4. Service-level breakdown: Anonymous Link Clues & Hints
        clues_stmt = select(
            func.count(case((AnonymousMessage.is_hint_unlocked == True, AnonymousMessage.id))).label("unlocked_hints"),
            func.count(case((AnonymousMessage.is_sender_clue_unlocked == True, AnonymousMessage.id))).label("unlocked_clues"),
            func.count(AnonymousMessage.id).label("total_messages")
        )
        res_clues = (await db.execute(clues_stmt)).first()
        unlocked_hints = int(res_clues.unlocked_hints or 0)
        unlocked_clues = int(res_clues.unlocked_clues or 0)
        anon_total_unlocked = unlocked_hints + unlocked_clues
        # 200 NGN per clue/hint unlock
        anon_revenue = round(anon_total_unlocked * 200.0, 2)

        anon_service_data = {
            "service_name": "Anonymous Clues & Hints",
            "total_revenue": anon_revenue,
            "successful_count": anon_total_unlocked,
            "pending_count": 0,
            "failed_count": 0,
            "total_orders": anon_total_unlocked
        }

        # If payment table revenue is recorded higher than inferred sum, ensure accurate aggregate
        all_services = [confess_service_data, celebration_service_data, anon_service_data]

        # 5. Recent transactions for real data visibility
        recent_stmt = select(Payment).order_by(Payment.created_at.desc()).limit(15)
        recent_res = await db.execute(recent_stmt)
        recent_payments = recent_res.scalars().all()

        recent_txs = [
            {
                "id": p.id,
                "reference": p.reference,
                "amount": p.amount,
                "currency": p.currency,
                "status": p.status,
                "channel": p.channel,
                "created_at": p.created_at,
                "paid_at": p.paid_at
            }
            for p in recent_payments
        ]

        return {
            "currency": "NGN",
            "total_revenue": round(total_revenue, 2),
            "total_orders": total_orders,
            "successful_transactions": successful_transactions,
            "pending_transactions": pending_transactions,
            "failed_transactions": failed_transactions,
            "abandoned_transactions": abandoned_transactions,
            "success_rate_percentage": success_rate,
            "average_order_value": average_order_value,
            "revenue_today": round(revenue_today, 2),
            "revenue_this_month": round(revenue_this_month, 2),
            "services_breakdown": all_services,
            "recent_transactions": recent_txs
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch financial analysis: {str(e)}"
        )
