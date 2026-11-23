from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime
from enum import Enum
from uuid import UUID

from typing import List
from app.schemas.user import UserResponse
from app.schemas.confess_form import ConfessFormResponse
from app.schemas.confess import AnonymousLinkResponse, AnonymousMessageResponse

class AdminCreate(BaseModel):
    email: EmailStr
    username: str
    password: str = Field(min_length=8)
    is_super_admin: bool = False

class AdminLogin(BaseModel):
    email: EmailStr
    password: str

class AdminResponse(BaseModel):
    id: UUID
    email: EmailStr
    username: str
    is_super_admin: bool
    created_at: datetime

    class Config:
        from_attributes = True

class AdminPasswordReset(BaseModel):
    email: EmailStr
    new_password: str = Field(min_length=8)

class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    admin: AdminResponse

class DashboardStatsResponse(BaseModel):
    total_users: int
    total_confess_forms: int
    total_anonymous_links: int
    total_messages: int

class ServiceFinancialBreakdown(BaseModel):
    service_name: str
    total_revenue: float
    successful_count: int
    pending_count: int
    failed_count: int
    total_orders: int

class RecentTransactionSummary(BaseModel):
    id: UUID
    reference: str
    amount: float
    currency: str
    status: str
    channel: Optional[str] = None
    created_at: datetime
    paid_at: Optional[datetime] = None

class FinancialAnalysisResponse(BaseModel):
    currency: str
    total_revenue: float
    total_orders: int
    successful_transactions: int
    pending_transactions: int
    failed_transactions: int
    abandoned_transactions: int
    success_rate_percentage: float
    average_order_value: float
    revenue_today: float
    revenue_this_month: float
    services_breakdown: List[ServiceFinancialBreakdown]
    recent_transactions: List[RecentTransactionSummary]

class AdminPaymentStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    ABANDONED = "abandoned"
    REVERSED = "reversed"
    REFUNDED = "refunded"

class AdminPaymentUpdateRequest(BaseModel):
    status: AdminPaymentStatus
    amount: Optional[float] = Field(default=None, gt=0)
    channel: Optional[str] = Field(default=None, max_length=64)
    paid_at: Optional[datetime] = None
    reason: Optional[str] = Field(default=None, max_length=500)

class AdminPaymentRecordResponse(BaseModel):
    id: UUID
    user_id: UUID
    reference: str
    amount: float
    currency: str
    status: str
    channel: Optional[str] = None
    paid_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class PaginatedPaymentsResponse(BaseModel):
    total: int
    items: List[AdminPaymentRecordResponse]

class PaginatedUsersResponse(BaseModel):
    total: int
    items: List[UserResponse]

class PaginatedConfessFormsResponse(BaseModel):
    total: int
    items: List[ConfessFormResponse]

class PaginatedAnonymousLinksResponse(BaseModel):
    total: int
    items: List[AnonymousLinkResponse]

class PaginatedAnonymousMessagesResponse(BaseModel):
    total: int
    items: List[AnonymousMessageResponse]
