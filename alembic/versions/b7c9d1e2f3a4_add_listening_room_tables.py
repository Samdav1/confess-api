"""Add Listening Room, Kindness Points, Little Perks, and Care Desk tables

Revision ID: b7c9d1e2f3a4
Revises: aac047a58ea4
Create Date: 2026-11-16 18:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
from sqlmodel import SQLModel

from app.models.listening_room import (
    ContentReport,
    GentleWord,
    KindnessPointsLedger,
    KindnessPointsPolicy,
    LittlePerk,
    ModerationAuditLog,
    PerkRedemption,
    Thought,
    ThoughtAcknowledgement,
    UserKindnessBalance,
)

# revision identifiers, used by Alembic.
revision: str = "b7c9d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "aac047a58ea4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LISTENING_ROOM_TABLES = [
    Thought.__table__,
    GentleWord.__table__,
    ThoughtAcknowledgement.__table__,
    KindnessPointsPolicy.__table__,
    UserKindnessBalance.__table__,
    KindnessPointsLedger.__table__,
    LittlePerk.__table__,
    PerkRedemption.__table__,
    ContentReport.__table__,
    ModerationAuditLog.__table__,
]


def upgrade() -> None:
    """Create Listening Room tables and indexes if not already present."""
    bind = op.get_bind()
    SQLModel.metadata.create_all(bind=bind, tables=_LISTENING_ROOM_TABLES, checkfirst=True)


def downgrade() -> None:
    """Drop Listening Room tables in reverse dependency order."""
    bind = op.get_bind()
    SQLModel.metadata.drop_all(bind=bind, tables=list(reversed(_LISTENING_ROOM_TABLES)), checkfirst=True)
