from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4
from sqlalchemy import Column, DateTime
from sqlmodel import SQLModel, Field, Relationship

class Payment(SQLModel, table=True):
    __tablename__ = "payments"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    reference: str = Field(unique=True, index=True)
    amount: float = Field(nullable=False)
    currency: str = Field(default="NGN")
    status: str = Field(default="pending", index=True)
    channel: Optional[str] = Field(default=None)
    paid_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc)
    )
