import base64
import hashlib
import json
import logging
import re
import secrets
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from cachetools import TTLCache
from fastapi import HTTPException, status
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.celebration import CelebrationPage, PaymentStatus
from app.models.listening_room import (
    AcknowledgementType,
    ContentReport,
    DiscountType,
    GentleWord,
    GentleWordStatus,
    KindnessPointsLedger,
    KindnessPointsPolicy,
    LedgerEntryType,
    LittlePerk,
    ModerationAuditLog,
    ModerationStatus,
    PerkRedemption,
    RedemptionStatus,
    ReportStatus,
    ReportTargetType,
    Thought,
    ThoughtAcknowledgement,
    ThoughtStatus,
    UserKindnessBalance,
)
from app.models.notification import NotificationType
from app.service.notification_service import notification_service

logger = logging.getLogger(__name__)

# Sliding-window style per-user rate limit caches (in-memory, low overhead)
_THOUGHT_RATE_CACHE: TTLCache = TTLCache(maxsize=50000, ttl=3600)
_GENTLE_WORD_RATE_CACHE: TTLCache = TTLCache(maxsize=50000, ttl=3600)
_REPORT_RATE_CACHE: TTLCache = TTLCache(maxsize=50000, ttl=3600)
_REDEEM_RATE_CACHE: TTLCache = TTLCache(maxsize=50000, ttl=3600)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_CRISIS_PATTERNS = re.compile(
    r"\b(kill\s+myself|end\s+my\s+life|suicid(?:e|al)|self[-\s]?harm|hurt\s+myself|want\s+to\s+die|don't\s+want\s+to\s+live)\b",
    re.IGNORECASE,
)

CRISIS_SUPPORT_RESOURCES = {
    "notice": (
        "If you are going through a difficult or overwhelming moment, please know you do not have to carry it alone. "
        "Confess is a supportive community space and not an emergency or clinical service."
    ),
    "helplines": [
        {"label": "Emergency Services", "contact": "Call your local emergency number (e.g., 112 / 911 / 999)"},
        {"label": "Nigeria (MANI / Mentally Aware)", "contact": "+234 809 111 6264"},
        {"label": "International Crisis Directory", "contact": "https://findahelpline.com"},
    ],
}

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _sanitize_text(text: str) -> str:
    cleaned = _HTML_TAG_RE.sub("", text)
    cleaned = _CONTROL_CHAR_RE.sub("", cleaned)
    return cleaned.strip()


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def _generate_redemption_code() -> str:
    parts = ["".join(secrets.choice(_CODE_ALPHABET) for _ in range(4)) for _ in range(3)]
    return f"LP-{'-'.join(parts)}"


def _encode_cursor(ts: Optional[datetime], item_id: UUID) -> str:
    ts_str = (ts or datetime.now(timezone.utc)).isoformat()
    payload = json.dumps({"ts": ts_str, "id": str(item_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> Tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        data = json.loads(raw)
        ts = datetime.fromisoformat(data["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts, UUID(data["id"])
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid pagination cursor.",
        )


def _check_rate_limit(cache: TTLCache, user_id: UUID, limit: int, action_label: str) -> None:
    key = str(user_id)
    count = cache.get(key, 0)
    if count >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded for {action_label}. Please wait before trying again.",
        )
    cache[key] = count + 1


class ListeningRoomService:
    """Service layer for The Listening Room, Care Desk, Kindness Points, and Little Perks."""

    async def get_or_create_policy(self, session: AsyncSession) -> KindnessPointsPolicy:
        result = await session.execute(select(KindnessPointsPolicy).where(KindnessPointsPolicy.id == 1))
        policy = result.scalar_one_or_none()
        if policy:
            return policy

        async with session.begin_nested():
            try:
                policy = KindnessPointsPolicy(id=1)
                session.add(policy)
                await session.flush()
                return policy
            except IntegrityError:
                pass

        result = await session.execute(select(KindnessPointsPolicy).where(KindnessPointsPolicy.id == 1))
        return result.scalar_one()

    async def _get_or_create_balance_locked(
        self, session: AsyncSession, user_id: UUID
    ) -> UserKindnessBalance:
        stmt = (
            select(UserKindnessBalance)
            .where(UserKindnessBalance.user_id == user_id)
            .with_for_update()
        )
        balance = (await session.execute(stmt)).scalar_one_or_none()
        if balance:
            return balance

        async with session.begin_nested():
            try:
                balance = UserKindnessBalance(user_id=user_id, balance=0, total_earned=0, total_redeemed=0)
                session.add(balance)
                await session.flush()
            except IntegrityError:
                pass

        return (await session.execute(stmt)).scalar_one()

    async def _check_posting_allowed(self, session: AsyncSession, user_id: UUID) -> None:
        stmt = select(UserKindnessBalance).where(UserKindnessBalance.user_id == user_id)
        bal = (await session.execute(stmt)).scalar_one_or_none()
        if bal and bal.posting_restricted_until:
            now = datetime.now(timezone.utc)
            restricted_until = bal.posting_restricted_until
            if restricted_until.tzinfo is None:
                restricted_until = restricted_until.replace(tzinfo=timezone.utc)
            if restricted_until > now:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Your posting privileges are temporarily restricted. Please contact support for details.",
                )

    async def _append_ledger_entry(
        self,
        session: AsyncSession,
        user_id: UUID,
        entry_type: str,
        points_delta: int,
        source_type: str,
        source_id: Optional[UUID],
        idempotency_key: str,
        description: str,
        admin_id: Optional[UUID] = None,
        allow_negative_balance: bool = False,
    ) -> Tuple[KindnessPointsLedger, UserKindnessBalance, bool]:
        """
        Atomically append an immutable ledger entry and update UserKindnessBalance.
        Returns (ledger_entry, balance_row, created_new).
        """
        if points_delta == 0:
            raise ValueError("points_delta must be non-zero")

        existing = (
            await session.execute(
                select(KindnessPointsLedger).where(KindnessPointsLedger.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()
        if existing:
            bal = await self._get_or_create_balance_locked(session, user_id)
            return existing, bal, False

        bal = await self._get_or_create_balance_locked(session, user_id)
        new_balance = bal.balance + points_delta
        if new_balance < 0 and not allow_negative_balance:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient Kindness Points balance.",
            )

        now = datetime.now(timezone.utc)
        bal.balance = new_balance
        if points_delta > 0 and entry_type in (LedgerEntryType.AWARD.value, LedgerEntryType.ADMIN_ADJUSTMENT.value):
            bal.total_earned += points_delta
        elif entry_type == LedgerEntryType.REDEMPTION.value and points_delta < 0:
            bal.total_redeemed += abs(points_delta)
        elif entry_type == LedgerEntryType.REFUND.value and points_delta > 0:
            bal.total_redeemed = max(0, bal.total_redeemed - points_delta)
        elif entry_type == LedgerEntryType.REVERSAL.value and points_delta < 0:
            bal.total_earned = max(0, bal.total_earned - abs(points_delta))
        bal.updated_at = now

        entry = KindnessPointsLedger(
            user_id=user_id,
            entry_type=entry_type,
            points_delta=points_delta,
            balance_after=new_balance,
            source_type=source_type,
            source_id=source_id,
            idempotency_key=idempotency_key,
            description=description,
            created_by_admin_id=admin_id,
            created_at=now,
        )
        session.add(bal)
        session.add(entry)
        await session.flush()
        return entry, bal, True

    async def _write_audit_log(
        self,
        session: AsyncSession,
        admin_id: UUID,
        action: str,
        target_type: str,
        target_id: UUID,
        previous_status: Optional[str] = None,
        new_status: Optional[str] = None,
        reason: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> ModerationAuditLog:
        log = ModerationAuditLog(
            admin_user_id=admin_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            previous_status=previous_status,
            new_status=new_status,
            reason=reason,
            metadata_=metadata,
        )
        session.add(log)
        return log

    async def _send_notification_safe(
        self,
        session: AsyncSession,
        user_id: UUID,
        notification_type: str,
        title: str,
        content: str,
        reference_id: Optional[UUID] = None,
        reference_type: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        try:
            await notification_service.create_notification(
                session=session,
                user_id=user_id,
                notification_type=notification_type,
                title=title,
                content=content,
                reference_id=reference_id,
                reference_type=reference_type,
                metadata=metadata,
            )
        except Exception as e:
            logger.error("Failed to create Listening Room notification (%s): %s", notification_type, e)

    # =========================================================================
    # MEMBER: THOUGHTS & LISTENING ROOM FEED
    # =========================================================================

    async def create_thought(
        self,
        session: AsyncSession,
        user_id: UUID,
        body: str,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self._check_posting_allowed(session, user_id)
        _check_rate_limit(_THOUGHT_RATE_CACHE, user_id, limit=5, action_label="sharing Thoughts")

        cleaned_body = _sanitize_text(body)
        if len(" ".join(cleaned_body.split())) < 10:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Thought must contain at least 10 non-whitespace characters.",
            )

        policy = await self.get_or_create_policy(session)
        now = datetime.now(timezone.utc)
        urgent_flagged = bool(_CRISIS_PATTERNS.search(cleaned_body))

        if policy.require_thought_moderation or urgent_flagged:
            t_status = ThoughtStatus.PENDING.value
            m_status = ModerationStatus.PENDING.value
            published_at = None
        else:
            t_status = ThoughtStatus.PUBLISHED.value
            m_status = ModerationStatus.APPROVED.value
            published_at = now

        thought = Thought(
            author_user_id=user_id,
            body=cleaned_body,
            category=category,
            status=t_status,
            moderation_status=m_status,
            is_urgent_flagged=urgent_flagged,
            created_at=now,
            updated_at=now,
            published_at=published_at,
        )
        session.add(thought)
        await session.commit()
        await session.refresh(thought)

        return {
            "id": thought.id,
            "body": thought.body,
            "category": thought.category,
            "status": thought.status,
            "moderation_status": thought.moderation_status,
            "gentle_words_count": thought.gentle_words_count,
            "created_at": thought.created_at,
            "published_at": thought.published_at,
            "support_resources": CRISIS_SUPPORT_RESOURCES if urgent_flagged else None,
        }

    async def _fetch_acknowledgements_map(
        self, session: AsyncSession, thought_ids: List[UUID]
    ) -> Dict[UUID, List[str]]:
        if not thought_ids:
            return {}
        stmt = (
            select(ThoughtAcknowledgement.thought_id, ThoughtAcknowledgement.acknowledgement_type)
            .where(ThoughtAcknowledgement.thought_id.in_(thought_ids))
            .distinct()
        )
        rows = (await session.execute(stmt)).all()
        ack_map: Dict[UUID, List[str]] = {tid: [] for tid in thought_ids}
        for tid, ack_type in rows:
            ack_map.setdefault(tid, []).append(ack_type)
        return ack_map

    async def list_published_thoughts(
        self,
        session: AsyncSession,
        category: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 20,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        stmt = select(Thought).where(
            Thought.status == ThoughtStatus.PUBLISHED.value,
            Thought.deleted_at.is_(None),
            Thought.published_at.isnot(None),
        )
        if category:
            stmt = stmt.where(Thought.category == category)

        if cursor:
            cursor_ts, cursor_id = _decode_cursor(cursor)
            stmt = stmt.where(
                or_(
                    Thought.published_at < cursor_ts,
                    and_(Thought.published_at == cursor_ts, Thought.id < cursor_id),
                )
            )

        stmt = stmt.order_by(Thought.published_at.desc(), Thought.id.desc()).limit(limit + 1)
        rows = (await session.execute(stmt)).scalars().all()

        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = _encode_cursor(items[-1].published_at, items[-1].id) if has_more and items else None

        ack_map = await self._fetch_acknowledgements_map(session, [t.id for t in items])

        serialized = [
            {
                "id": t.id,
                "body": t.body,
                "category": t.category,
                "created_at": t.created_at,
                "published_at": t.published_at,
                "gentle_words_count": t.gentle_words_count,
                "acknowledgements": ack_map.get(t.id, []),
            }
            for t in items
        ]
        return serialized, next_cursor

    async def get_thought_detail(self, session: AsyncSession, thought_id: UUID) -> Dict[str, Any]:
        stmt = select(Thought).where(
            Thought.id == thought_id,
            Thought.status == ThoughtStatus.PUBLISHED.value,
            Thought.deleted_at.is_(None),
        )
        thought = (await session.execute(stmt)).scalar_one_or_none()
        if not thought:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thought not found or not publicly available.",
            )

        gw_stmt = (
            select(GentleWord)
            .where(
                GentleWord.thought_id == thought_id,
                GentleWord.status == GentleWordStatus.APPROVED.value,
                GentleWord.deleted_at.is_(None),
            )
            .order_by(GentleWord.approved_at.asc(), GentleWord.created_at.asc())
        )
        gentle_words = (await session.execute(gw_stmt)).scalars().all()
        ack_map = await self._fetch_acknowledgements_map(session, [thought.id])

        return {
            "id": thought.id,
            "body": thought.body,
            "category": thought.category,
            "created_at": thought.created_at,
            "published_at": thought.published_at,
            "gentle_words_count": thought.gentle_words_count,
            "acknowledgements": ack_map.get(thought.id, []),
            "gentle_words": [
                {
                    "id": gw.id,
                    "thought_id": gw.thought_id,
                    "body": gw.body,
                    "created_at": gw.created_at,
                    "approved_at": gw.approved_at,
                }
                for gw in gentle_words
            ],
            "support_resources": CRISIS_SUPPORT_RESOURCES if thought.is_urgent_flagged else None,
        }

    async def list_my_thoughts(
        self,
        session: AsyncSession,
        user_id: UUID,
        cursor: Optional[str] = None,
        limit: int = 20,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        stmt = select(Thought).where(
            Thought.author_user_id == user_id,
            Thought.status != ThoughtStatus.DELETED.value,
            Thought.deleted_at.is_(None),
        )
        if cursor:
            cursor_ts, cursor_id = _decode_cursor(cursor)
            stmt = stmt.where(
                or_(
                    Thought.created_at < cursor_ts,
                    and_(Thought.created_at == cursor_ts, Thought.id < cursor_id),
                )
            )
        stmt = stmt.order_by(Thought.created_at.desc(), Thought.id.desc()).limit(limit + 1)
        rows = (await session.execute(stmt)).scalars().all()

        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = _encode_cursor(items[-1].created_at, items[-1].id) if has_more and items else None

        serialized = [
            {
                "id": t.id,
                "body": t.body,
                "category": t.category,
                "status": t.status,
                "moderation_status": t.moderation_status,
                "gentle_words_count": t.gentle_words_count,
                "created_at": t.created_at,
                "published_at": t.published_at,
                "support_resources": CRISIS_SUPPORT_RESOURCES if t.is_urgent_flagged else None,
            }
            for t in items
        ]
        return serialized, next_cursor

    async def delete_my_thought(self, session: AsyncSession, user_id: UUID, thought_id: UUID) -> Dict[str, Any]:
        stmt = select(Thought).where(Thought.id == thought_id).with_for_update()
        thought = (await session.execute(stmt)).scalar_one_or_none()
        if not thought or thought.status == ThoughtStatus.DELETED.value:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thought not found.")
        if thought.author_user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this Thought.")

        now = datetime.now(timezone.utc)
        thought.status = ThoughtStatus.DELETED.value
        thought.deleted_at = now
        thought.updated_at = now
        session.add(thought)
        await session.commit()
        return {"id": thought.id, "status": thought.status}

    async def add_acknowledgement(
        self,
        session: AsyncSession,
        user_id: UUID,
        thought_id: UUID,
        ack_type: AcknowledgementType,
    ) -> Dict[str, Any]:
        thought = (
            await session.execute(
                select(Thought).where(
                    Thought.id == thought_id,
                    Thought.status == ThoughtStatus.PUBLISHED.value,
                    Thought.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if not thought:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thought not found.")

        existing = (
            await session.execute(
                select(ThoughtAcknowledgement).where(
                    ThoughtAcknowledgement.thought_id == thought_id,
                    ThoughtAcknowledgement.user_id == user_id,
                    ThoughtAcknowledgement.acknowledgement_type == ack_type.value,
                )
            )
        ).scalar_one_or_none()

        if not existing:
            ack = ThoughtAcknowledgement(
                thought_id=thought_id,
                user_id=user_id,
                acknowledgement_type=ack_type.value,
            )
            session.add(ack)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()

        ack_map = await self._fetch_acknowledgements_map(session, [thought_id])
        return {"thought_id": thought_id, "acknowledgements": ack_map.get(thought_id, [])}

    # =========================================================================
    # MEMBER: GENTLE WORDS
    # =========================================================================

    async def submit_gentle_word(
        self,
        session: AsyncSession,
        user_id: UUID,
        thought_id: UUID,
        body: str,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self._check_posting_allowed(session, user_id)

        if idempotency_key:
            scoped_key = f"gw_submit:{user_id}:{idempotency_key}"
            existing = (
                await session.execute(select(GentleWord).where(GentleWord.idempotency_key == scoped_key))
            ).scalar_one_or_none()
            if existing:
                return {
                    "id": existing.id,
                    "thought_id": existing.thought_id,
                    "body": existing.body,
                    "status": existing.status,
                    "created_at": existing.created_at,
                }
        else:
            scoped_key = None

        _check_rate_limit(_GENTLE_WORD_RATE_CACHE, user_id, limit=15, action_label="submitting Gentle Words")

        thought = (
            await session.execute(
                select(Thought).where(
                    Thought.id == thought_id,
                    Thought.status == ThoughtStatus.PUBLISHED.value,
                    Thought.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if not thought:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Thought not found or not available for Gentle Words.",
            )

        cleaned_body = _sanitize_text(body)
        if len(" ".join(cleaned_body.split())) < 5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Gentle Word must contain at least 5 non-whitespace characters.",
            )

        # Duplicate spam check within the last hour
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        dup = (
            await session.execute(
                select(GentleWord).where(
                    GentleWord.thought_id == thought_id,
                    GentleWord.author_user_id == user_id,
                    GentleWord.body == cleaned_body,
                    GentleWord.created_at >= one_hour_ago,
                )
            )
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You have already submitted this Gentle Word recently.",
            )

        now = datetime.now(timezone.utc)
        gw = GentleWord(
            thought_id=thought_id,
            author_user_id=user_id,
            body=cleaned_body,
            status=GentleWordStatus.PENDING.value,
            idempotency_key=scoped_key,
            created_at=now,
            updated_at=now,
        )
        session.add(gw)
        await session.commit()
        await session.refresh(gw)

        return {
            "id": gw.id,
            "thought_id": gw.thought_id,
            "body": gw.body,
            "status": gw.status,
            "created_at": gw.created_at,
        }

    async def list_my_gentle_words(
        self,
        session: AsyncSession,
        user_id: UUID,
        cursor: Optional[str] = None,
        limit: int = 20,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        stmt = select(GentleWord).where(
            GentleWord.author_user_id == user_id,
            GentleWord.status != GentleWordStatus.DELETED.value,
            GentleWord.deleted_at.is_(None),
        )
        if cursor:
            cursor_ts, cursor_id = _decode_cursor(cursor)
            stmt = stmt.where(
                or_(
                    GentleWord.created_at < cursor_ts,
                    and_(GentleWord.created_at == cursor_ts, GentleWord.id < cursor_id),
                )
            )
        stmt = stmt.order_by(GentleWord.created_at.desc(), GentleWord.id.desc()).limit(limit + 1)
        rows = (await session.execute(stmt)).scalars().all()

        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = _encode_cursor(items[-1].created_at, items[-1].id) if has_more and items else None

        serialized = [
            {
                "id": gw.id,
                "thought_id": gw.thought_id,
                "body": gw.body,
                "status": gw.status,
                "points_awarded": gw.points_awarded,
                "created_at": gw.created_at,
                "approved_at": gw.approved_at,
            }
            for gw in items
        ]
        return serialized, next_cursor

    # =========================================================================
    # MEMBER: CONTENT REPORTS
    # =========================================================================

    async def create_report(
        self,
        session: AsyncSession,
        user_id: UUID,
        target_type: ReportTargetType,
        target_id: UUID,
        reason: str,
        details: Optional[str] = None,
    ) -> Dict[str, Any]:
        _check_rate_limit(_REPORT_RATE_CACHE, user_id, limit=20, action_label="reporting content")

        if target_type == ReportTargetType.THOUGHT:
            target = (
                await session.execute(
                    select(Thought).where(Thought.id == target_id, Thought.deleted_at.is_(None))
                )
            ).scalar_one_or_none()
        else:
            target = (
                await session.execute(
                    select(GentleWord).where(GentleWord.id == target_id, GentleWord.deleted_at.is_(None))
                )
            ).scalar_one_or_none()

        if not target:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reported content not found.")

        existing = (
            await session.execute(
                select(ContentReport).where(
                    ContentReport.reporter_user_id == user_id,
                    ContentReport.target_type == target_type.value,
                    ContentReport.target_id == target_id,
                    ContentReport.status == ReportStatus.PENDING.value,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return {
                "id": existing.id,
                "target_type": existing.target_type,
                "target_id": existing.target_id,
                "reason": existing.reason,
                "status": existing.status,
                "created_at": existing.created_at,
            }

        report = ContentReport(
            reporter_user_id=user_id,
            target_type=target_type.value,
            target_id=target_id,
            reason=reason,
            details=_sanitize_text(details) if details else None,
            status=ReportStatus.PENDING.value,
        )
        session.add(report)
        await session.commit()
        await session.refresh(report)

        return {
            "id": report.id,
            "target_type": report.target_type,
            "target_id": report.target_id,
            "reason": report.reason,
            "status": report.status,
            "created_at": report.created_at,
        }

    # =========================================================================
    # MEMBER: KINDNESS POINTS & LITTLE PERKS
    # =========================================================================

    async def get_my_kindness_points(
        self,
        session: AsyncSession,
        user_id: UUID,
        cursor: Optional[str] = None,
        limit: int = 20,
    ) -> Dict[str, Any]:
        policy = await self.get_or_create_policy(session)
        bal = (
            await session.execute(select(UserKindnessBalance).where(UserKindnessBalance.user_id == user_id))
        ).scalar_one_or_none()

        # Reconcile/compute from append-only ledger if balance row doesn't exist yet
        ledger_sum = (
            await session.execute(
                select(func.coalesce(func.sum(KindnessPointsLedger.points_delta), 0)).where(
                    KindnessPointsLedger.user_id == user_id
                )
            )
        ).scalar() or 0

        stmt = select(KindnessPointsLedger).where(KindnessPointsLedger.user_id == user_id)
        if cursor:
            cursor_ts, cursor_id = _decode_cursor(cursor)
            stmt = stmt.where(
                or_(
                    KindnessPointsLedger.created_at < cursor_ts,
                    and_(KindnessPointsLedger.created_at == cursor_ts, KindnessPointsLedger.id < cursor_id),
                )
            )
        stmt = stmt.order_by(KindnessPointsLedger.created_at.desc(), KindnessPointsLedger.id.desc()).limit(limit + 1)
        rows = (await session.execute(stmt)).scalars().all()

        has_more = len(rows) > limit
        entries = rows[:limit]
        next_cursor = _encode_cursor(entries[-1].created_at, entries[-1].id) if has_more and entries else None

        return {
            "balance": int(bal.balance if bal else ledger_sum),
            "total_earned": int(bal.total_earned if bal else 0),
            "total_redeemed": int(bal.total_redeemed if bal else 0),
            "minimum_points_to_redeem": policy.minimum_points_to_redeem,
            "entries": [
                {
                    "id": e.id,
                    "type": e.entry_type,
                    "points_delta": e.points_delta,
                    "balance_after": e.balance_after,
                    "source_type": e.source_type,
                    "description": e.description,
                    "created_at": e.created_at,
                }
                for e in entries
            ],
            "next_cursor": next_cursor,
        }

    async def list_active_perks(self, session: AsyncSession) -> List[LittlePerk]:
        now = datetime.now(timezone.utc)
        stmt = (
            select(LittlePerk)
            .where(
                LittlePerk.active == True,
                or_(LittlePerk.starts_at.is_(None), LittlePerk.starts_at <= now),
                or_(LittlePerk.ends_at.is_(None), LittlePerk.ends_at >= now),
            )
            .order_by(LittlePerk.points_cost.asc(), LittlePerk.created_at.desc())
        )
        return list((await session.execute(stmt)).scalars().all())

    async def redeem_perk(
        self,
        session: AsyncSession,
        user_id: UUID,
        perk_id: UUID,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        # Fast-path idempotency check
        existing = (
            await session.execute(
                select(PerkRedemption).where(PerkRedemption.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()
        if existing:
            if existing.user_id != user_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency key belongs to another request.",
                )
            perk = await session.get(LittlePerk, existing.perk_id)
            return self._serialize_redemption(existing, perk.name if perk else None)

        _check_rate_limit(_REDEEM_RATE_CACHE, user_id, limit=10, action_label="redeeming Little Perks")

        perk = (
            await session.execute(select(LittlePerk).where(LittlePerk.id == perk_id).with_for_update())
        ).scalar_one_or_none()
        if not perk or not perk.active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Little Perk offer not found or inactive.")

        now = datetime.now(timezone.utc)
        if perk.starts_at and perk.starts_at > now:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This Little Perk offer has not started yet.")
        if perk.ends_at and perk.ends_at < now:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This Little Perk offer has expired.")
        if perk.usage_limit_total is not None and perk.times_redeemed >= perk.usage_limit_total:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This Little Perk offer is no longer available.")

        if perk.usage_limit_per_user is not None:
            user_redemption_count = (
                await session.execute(
                    select(func.count(PerkRedemption.id)).where(
                        PerkRedemption.user_id == user_id,
                        PerkRedemption.perk_id == perk_id,
                        PerkRedemption.status.notin_(
                            [RedemptionStatus.CANCELLED.value, RedemptionStatus.REFUNDED.value]
                        ),
                    )
                )
            ).scalar() or 0
            if user_redemption_count >= perk.usage_limit_per_user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="You have reached the redemption limit for this Little Perk.",
                )

        policy = await self.get_or_create_policy(session)
        bal = await self._get_or_create_balance_locked(session, user_id)

        if bal.balance < policy.minimum_points_to_redeem:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"You need at least {policy.minimum_points_to_redeem} Kindness Points to redeem perks.",
            )
        if bal.balance < perk.points_cost:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient Kindness Points balance for this Little Perk.",
            )

        code = _generate_redemption_code()
        code_hash = _hash_code(code)

        _, _, _ = await self._append_ledger_entry(
            session=session,
            user_id=user_id,
            entry_type=LedgerEntryType.REDEMPTION.value,
            points_delta=-perk.points_cost,
            source_type="little_perk",
            source_id=perk.id,
            idempotency_key=f"perk_redeem:{idempotency_key}",
            description=f"Redeemed Little Perk: {perk.name}",
        )

        redemption = PerkRedemption(
            user_id=user_id,
            perk_id=perk.id,
            points_spent=perk.points_cost,
            code=code,
            code_hash=code_hash,
            status=RedemptionStatus.AVAILABLE.value,
            currency=perk.currency,
            expires_at=perk.ends_at,
            redeemed_at=now,
            created_at=now,
            updated_at=now,
            idempotency_key=idempotency_key,
        )
        perk.times_redeemed += 1
        perk.updated_at = now
        session.add(redemption)
        session.add(perk)
        await session.flush()

        await self._send_notification_safe(
            session=session,
            user_id=user_id,
            notification_type=NotificationType.PERK_REDEEMED.value,
            title="Your Little Perk is ready to use",
            content=f"You redeemed '{perk.name}'. Apply it when paying for an eligible celebration.",
            reference_id=redemption.id,
            reference_type="perk_redemption",
            metadata={"perk_id": str(perk.id)},
        )

        await session.commit()
        await session.refresh(redemption)
        return self._serialize_redemption(redemption, perk.name)

    def _serialize_redemption(self, r: PerkRedemption, perk_name: Optional[str] = None) -> Dict[str, Any]:
        return {
            "id": r.id,
            "perk_id": r.perk_id,
            "perk_name": perk_name,
            "points_spent": r.points_spent,
            "code": r.code,
            "status": r.status,
            "discount_amount": r.discount_amount,
            "currency": r.currency,
            "celebration_id": r.celebration_id,
            "expires_at": r.expires_at,
            "redeemed_at": r.redeemed_at,
            "created_at": r.created_at,
        }

    async def list_my_redemptions(self, session: AsyncSession, user_id: UUID) -> List[Dict[str, Any]]:
        stmt = (
            select(PerkRedemption, LittlePerk.name)
            .outerjoin(LittlePerk, PerkRedemption.perk_id == LittlePerk.id)
            .where(PerkRedemption.user_id == user_id)
            .order_by(PerkRedemption.created_at.desc())
        )
        rows = (await session.execute(stmt)).all()
        return [self._serialize_redemption(r, p_name) for r, p_name in rows]

    # =========================================================================
    # CHECKOUT LIFECYCLE (APPLY / RESERVE / CONSUME / RELEASE / REFUND)
    # =========================================================================

    def _compute_discount(self, perk: LittlePerk, order_amount: float) -> float:
        if perk.discount_type == DiscountType.PERCENTAGE.value:
            discount = order_amount * (perk.discount_value / 100.0)
            if perk.maximum_discount_amount is not None:
                discount = min(discount, float(perk.maximum_discount_amount))
        else:
            discount = float(perk.discount_value)
        return round(min(max(0.0, discount), order_amount), 2)

    async def apply_redemption_at_checkout(
        self,
        session: AsyncSession,
        user_id: UUID,
        code: str,
        celebration_id: UUID,
        payment_reference: Optional[str] = None,
        auto_commit: bool = True,
    ) -> Dict[str, Any]:
        code_hash = _hash_code(code)
        redemption = (
            await session.execute(
                select(PerkRedemption).where(PerkRedemption.code_hash == code_hash).with_for_update()
            )
        ).scalar_one_or_none()
        if not redemption:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Redemption code not found.")
        if redemption.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This Little Perk does not belong to your account.")

        celebration = (
            await session.execute(
                select(CelebrationPage).where(CelebrationPage.id == celebration_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not celebration:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Celebration page not found.")
        if celebration.created_by != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to apply a perk to this celebration.")
        if celebration.payment_status == PaymentStatus.PAID:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Celebration page is already paid.")

        # Idempotent if already reserved for this exact celebration
        if redemption.status == RedemptionStatus.RESERVED.value and redemption.celebration_id == celebration_id:
            if payment_reference and redemption.payment_reference != payment_reference:
                redemption.payment_reference = payment_reference
                redemption.updated_at = datetime.now(timezone.utc)
                session.add(redemption)
                if auto_commit:
                    await session.commit()
            return {
                "redemption_id": redemption.id,
                "code": redemption.code,
                "status": redemption.status,
                "celebration_id": celebration_id,
                "original_amount": float(redemption.original_order_amount or celebration.total_price),
                "discount_amount": float(redemption.discount_amount or 0.0),
                "final_amount": float(redemption.final_order_amount or celebration.total_price),
                "currency": redemption.currency or "NGN",
            }

        if redemption.status != RedemptionStatus.AVAILABLE.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Redemption cannot be applied because its status is '{redemption.status}'.",
            )

        now = datetime.now(timezone.utc)
        if redemption.expires_at:
            exp = redemption.expires_at if redemption.expires_at.tzinfo else redemption.expires_at.replace(tzinfo=timezone.utc)
            if exp < now:
                redemption.status = RedemptionStatus.EXPIRED.value
                redemption.updated_at = now
                session.add(redemption)
                if auto_commit:
                    await session.commit()
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This Little Perk redemption has expired.")

        # Prevent stacking multiple perks on the same celebration
        existing_for_celebration = (
            await session.execute(
                select(PerkRedemption).where(
                    PerkRedemption.celebration_id == celebration_id,
                    PerkRedemption.status.in_([RedemptionStatus.RESERVED.value, RedemptionStatus.USED.value]),
                    PerkRedemption.id != redemption.id,
                )
            )
        ).scalar_one_or_none()
        if existing_for_celebration:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Another Little Perk is already applied to this celebration.",
            )

        perk = await session.get(LittlePerk, redemption.perk_id)
        if not perk or not perk.active:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Associated Little Perk offer is no longer active.")

        if perk.eligible_celebration_types:
            occasion_val = celebration.occasion_type.value if hasattr(celebration.occasion_type, "value") else str(celebration.occasion_type)
            if occasion_val not in perk.eligible_celebration_types:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="This Little Perk is not eligible for this celebration occasion.",
                )

        if perk.currency and redemption.currency and perk.currency != redemption.currency:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Currency mismatch for Little Perk.")

        original_amount = float(celebration.total_price)
        if perk.minimum_order_amount is not None and original_amount < float(perk.minimum_order_amount):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Minimum order amount of {perk.minimum_order_amount} {redemption.currency or 'NGN'} is required for this Little Perk.",
            )

        discount_amount = self._compute_discount(perk, original_amount)
        final_amount = round(max(0.0, original_amount - discount_amount), 2)

        redemption.status = RedemptionStatus.RESERVED.value
        redemption.celebration_id = celebration_id
        redemption.payment_reference = payment_reference
        redemption.original_order_amount = original_amount
        redemption.discount_amount = discount_amount
        redemption.final_order_amount = final_amount
        redemption.reserved_at = now
        redemption.updated_at = now
        session.add(redemption)
        await session.flush()

        if auto_commit:
            await session.commit()

        return {
            "redemption_id": redemption.id,
            "code": redemption.code,
            "status": redemption.status,
            "celebration_id": celebration_id,
            "original_amount": original_amount,
            "discount_amount": discount_amount,
            "final_amount": final_amount,
            "currency": redemption.currency or "NGN",
        }

    async def consume_redemption(
        self,
        session: AsyncSession,
        redemption_id: UUID,
        payment_reference: Optional[str] = None,
        auto_commit: bool = True,
    ) -> Optional[PerkRedemption]:
        redemption = (
            await session.execute(
                select(PerkRedemption).where(PerkRedemption.id == redemption_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not redemption:
            return None

        if redemption.status == RedemptionStatus.USED.value:
            return redemption

        if redemption.status not in (RedemptionStatus.RESERVED.value, RedemptionStatus.AVAILABLE.value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot consume redemption in status '{redemption.status}'.",
            )

        now = datetime.now(timezone.utc)
        redemption.status = RedemptionStatus.USED.value
        redemption.used_at = now
        redemption.updated_at = now
        if payment_reference:
            redemption.payment_reference = payment_reference
        session.add(redemption)
        await session.flush()

        if auto_commit:
            await session.commit()
        return redemption

    async def release_redemption(
        self,
        session: AsyncSession,
        redemption_id: UUID,
        auto_commit: bool = True,
    ) -> Optional[PerkRedemption]:
        redemption = (
            await session.execute(
                select(PerkRedemption).where(PerkRedemption.id == redemption_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not redemption:
            return None

        if redemption.status == RedemptionStatus.AVAILABLE.value:
            return redemption

        if redemption.status != RedemptionStatus.RESERVED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot release redemption in status '{redemption.status}'.",
            )

        now = datetime.now(timezone.utc)
        redemption.status = RedemptionStatus.AVAILABLE.value
        redemption.celebration_id = None
        redemption.payment_reference = None
        redemption.reserved_at = None
        redemption.original_order_amount = None
        redemption.discount_amount = None
        redemption.final_order_amount = None
        redemption.updated_at = now
        session.add(redemption)
        await session.flush()

        if auto_commit:
            await session.commit()
        return redemption

    async def refund_redemption_on_payment_reversal(
        self,
        session: AsyncSession,
        redemption_id: UUID,
        auto_commit: bool = True,
    ) -> Optional[PerkRedemption]:
        redemption = (
            await session.execute(
                select(PerkRedemption).where(PerkRedemption.id == redemption_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not redemption:
            return None

        if redemption.status == RedemptionStatus.REFUNDED.value:
            return redemption

        if redemption.status == RedemptionStatus.RESERVED.value:
            return await self.release_redemption(session, redemption_id, auto_commit=auto_commit)

        if redemption.status != RedemptionStatus.USED.value:
            return redemption

        now = datetime.now(timezone.utc)
        redemption.status = RedemptionStatus.REFUNDED.value
        redemption.updated_at = now
        session.add(redemption)

        await self._append_ledger_entry(
            session=session,
            user_id=redemption.user_id,
            entry_type=LedgerEntryType.REFUND.value,
            points_delta=redemption.points_spent,
            source_type="perk_redemption",
            source_id=redemption.id,
            idempotency_key=f"redemption_refund:{redemption.id}",
            description=f"Points refunded after payment reversal for code {redemption.code}",
        )
        await session.flush()

        if auto_commit:
            await session.commit()
        return redemption

    # =========================================================================
    # ADMIN / CARE DESK MODERATION & ANALYTICS
    # =========================================================================

    async def list_care_desk_thoughts(
        self,
        session: AsyncSession,
        thought_status: Optional[str] = ThoughtStatus.PENDING.value,
        skip: int = 0,
        limit: int = 50,
    ) -> Dict[str, Any]:
        filters = [Thought.deleted_at.is_(None)]
        if thought_status:
            filters.append(Thought.status == thought_status)

        total = (
            await session.execute(select(func.count(Thought.id)).where(*filters))
        ).scalar() or 0

        stmt = (
            select(Thought)
            .where(*filters)
            .order_by(Thought.is_urgent_flagged.desc(), Thought.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
        items = list((await session.execute(stmt)).scalars().all())
        return {"total": int(total), "items": items}

    async def list_care_desk_gentle_words(
        self,
        session: AsyncSession,
        gw_status: Optional[str] = GentleWordStatus.PENDING.value,
        skip: int = 0,
        limit: int = 50,
    ) -> Dict[str, Any]:
        filters = [GentleWord.deleted_at.is_(None)]
        if gw_status:
            filters.append(GentleWord.status == gw_status)

        total = (
            await session.execute(select(func.count(GentleWord.id)).where(*filters))
        ).scalar() or 0

        stmt = (
            select(GentleWord, Thought.body)
            .outerjoin(Thought, GentleWord.thought_id == Thought.id)
            .where(*filters)
            .order_by(GentleWord.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
        items = [
            {
                "id": gw.id,
                "thought_id": gw.thought_id,
                "thought_preview": (t_body[:160] + "...") if t_body and len(t_body) > 160 else t_body,
                "author_user_id": gw.author_user_id,
                "body": gw.body,
                "status": gw.status,
                "points_awarded": gw.points_awarded,
                "moderation_note": gw.moderation_note,
                "moderated_by": gw.moderated_by,
                "moderated_at": gw.moderated_at,
                "created_at": gw.created_at,
                "approved_at": gw.approved_at,
            }
            for gw, t_body in rows
        ]
        return {"total": int(total), "items": items}

    async def approve_thought(
        self,
        session: AsyncSession,
        admin_id: UUID,
        thought_id: UUID,
        note: Optional[str] = None,
    ) -> Thought:
        thought = (
            await session.execute(select(Thought).where(Thought.id == thought_id).with_for_update())
        ).scalar_one_or_none()
        if not thought or thought.status == ThoughtStatus.DELETED.value:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thought not found.")

        if thought.status == ThoughtStatus.PUBLISHED.value:
            return thought

        if thought.status not in (ThoughtStatus.PENDING.value, ThoughtStatus.HIDDEN.value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot approve Thought in status '{thought.status}'.",
            )

        now = datetime.now(timezone.utc)
        prev_status = thought.status
        thought.status = ThoughtStatus.PUBLISHED.value
        thought.moderation_status = ModerationStatus.APPROVED.value
        thought.moderated_by = admin_id
        thought.moderated_at = now
        if note is not None:
            thought.moderation_note = note
        if not thought.published_at:
            thought.published_at = now
        thought.updated_at = now
        session.add(thought)

        await self._write_audit_log(
            session=session,
            admin_id=admin_id,
            action="thought_approve",
            target_type="thought",
            target_id=thought.id,
            previous_status=prev_status,
            new_status=thought.status,
            reason=note,
        )

        if prev_status == ThoughtStatus.PENDING.value:
            await self._send_notification_safe(
                session=session,
                user_id=thought.author_user_id,
                notification_type=NotificationType.THOUGHT_PUBLISHED.value,
                title="Your Thought is now in The Listening Room",
                content="Your Thought has been reviewed and shared anonymously in The Listening Room.",
                reference_id=thought.id,
                reference_type="thought",
            )

        await session.commit()
        await session.refresh(thought)
        return thought

    async def reject_thought(
        self,
        session: AsyncSession,
        admin_id: UUID,
        thought_id: UUID,
        reason: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Thought:
        thought = (
            await session.execute(select(Thought).where(Thought.id == thought_id).with_for_update())
        ).scalar_one_or_none()
        if not thought or thought.status == ThoughtStatus.DELETED.value:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thought not found.")

        if thought.status == ThoughtStatus.REJECTED.value:
            return thought

        if thought.status != ThoughtStatus.PENDING.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot reject Thought in status '{thought.status}'. Use hide for published content.",
            )

        now = datetime.now(timezone.utc)
        prev_status = thought.status
        thought.status = ThoughtStatus.REJECTED.value
        thought.moderation_status = ModerationStatus.REJECTED.value
        thought.moderated_by = admin_id
        thought.moderated_at = now
        thought.moderation_note = note or reason
        thought.updated_at = now
        session.add(thought)

        await self._write_audit_log(
            session=session,
            admin_id=admin_id,
            action="thought_reject",
            target_type="thought",
            target_id=thought.id,
            previous_status=prev_status,
            new_status=thought.status,
            reason=reason or note,
        )
        await session.commit()
        await session.refresh(thought)
        return thought

    async def _calculate_award_points_with_caps(
        self,
        session: AsyncSession,
        user_id: UUID,
        policy: KindnessPointsPolicy,
        is_self_reply: bool,
    ) -> int:
        if is_self_reply or policy.points_per_gentle_word <= 0:
            return 0

        now = datetime.now(timezone.utc)
        day_ago = now - timedelta(hours=24)
        week_ago = now - timedelta(days=7)

        daily_awarded = (
            await session.execute(
                select(func.coalesce(func.sum(KindnessPointsLedger.points_delta), 0)).where(
                    KindnessPointsLedger.user_id == user_id,
                    KindnessPointsLedger.entry_type == LedgerEntryType.AWARD.value,
                    KindnessPointsLedger.created_at >= day_ago,
                )
            )
        ).scalar() or 0

        weekly_awarded = (
            await session.execute(
                select(func.coalesce(func.sum(KindnessPointsLedger.points_delta), 0)).where(
                    KindnessPointsLedger.user_id == user_id,
                    KindnessPointsLedger.entry_type == LedgerEntryType.AWARD.value,
                    KindnessPointsLedger.created_at >= week_ago,
                )
            )
        ).scalar() or 0

        rem_daily = max(0, policy.daily_award_cap - int(daily_awarded)) if policy.daily_award_cap > 0 else policy.points_per_gentle_word
        rem_weekly = max(0, policy.weekly_award_cap - int(weekly_awarded)) if policy.weekly_award_cap > 0 else policy.points_per_gentle_word

        return max(0, min(policy.points_per_gentle_word, rem_daily, rem_weekly))

    async def approve_gentle_word(
        self,
        session: AsyncSession,
        admin_id: UUID,
        gentle_word_id: UUID,
        note: Optional[str] = None,
    ) -> GentleWord:
        gw = (
            await session.execute(select(GentleWord).where(GentleWord.id == gentle_word_id).with_for_update())
        ).scalar_one_or_none()
        if not gw or gw.status == GentleWordStatus.DELETED.value:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gentle Word not found.")

        # Idempotent repeat approval
        if gw.status == GentleWordStatus.APPROVED.value:
            return gw

        if gw.status != GentleWordStatus.PENDING.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot approve Gentle Word in status '{gw.status}'.",
            )

        thought = (
            await session.execute(select(Thought).where(Thought.id == gw.thought_id).with_for_update())
        ).scalar_one_or_none()
        if not thought or thought.status == ThoughtStatus.DELETED.value:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent Thought not found.")

        policy = await self.get_or_create_policy(session)
        is_self_reply = gw.author_user_id == thought.author_user_id
        awarded_pts = await self._calculate_award_points_with_caps(session, gw.author_user_id, policy, is_self_reply)

        now = datetime.now(timezone.utc)
        prev_status = gw.status
        gw.status = GentleWordStatus.APPROVED.value
        gw.moderated_by = admin_id
        gw.moderated_at = now
        gw.approved_at = now
        gw.moderation_note = note
        gw.points_awarded = awarded_pts
        gw.updated_at = now
        session.add(gw)

        thought.gentle_words_count += 1
        thought.updated_at = now
        session.add(thought)

        if awarded_pts > 0:
            await self._append_ledger_entry(
                session=session,
                user_id=gw.author_user_id,
                entry_type=LedgerEntryType.AWARD.value,
                points_delta=awarded_pts,
                source_type="gentle_word",
                source_id=gw.id,
                idempotency_key=f"gw_award:{gw.id}",
                description="A Gentle Word approved",
                admin_id=admin_id,
            )

        await self._write_audit_log(
            session=session,
            admin_id=admin_id,
            action="gentle_word_approve",
            target_type="gentle_word",
            target_id=gw.id,
            previous_status=prev_status,
            new_status=gw.status,
            reason=note,
            metadata={"points_awarded": awarded_pts},
        )

        # Contributor notification
        contrib_msg = (
            f"Your Gentle Word has been shared. You earned {awarded_pts} Kindness Points."
            if awarded_pts > 0
            else "Your Gentle Word has been shared in The Listening Room."
        )
        await self._send_notification_safe(
            session=session,
            user_id=gw.author_user_id,
            notification_type=NotificationType.GENTLE_WORD_APPROVED.value,
            title="Your Gentle Word was approved",
            content=contrib_msg,
            reference_id=gw.id,
            reference_type="gentle_word",
            metadata={"thought_id": str(thought.id), "points_awarded": awarded_pts},
        )

        # Thought author notification (privacy-safe: never exposes contributor identity)
        if not is_self_reply:
            await self._send_notification_safe(
                session=session,
                user_id=thought.author_user_id,
                notification_type=NotificationType.GENTLE_WORD_RECEIVED.value,
                title="Someone left a Gentle Word for your Thought",
                content="Someone left a Gentle Word for your Thought in The Listening Room.",
                reference_id=thought.id,
                reference_type="thought",
            )

        await session.commit()
        await session.refresh(gw)
        return gw

    async def reject_gentle_word(
        self,
        session: AsyncSession,
        admin_id: UUID,
        gentle_word_id: UUID,
        reason: Optional[str] = None,
        note: Optional[str] = None,
    ) -> GentleWord:
        gw = (
            await session.execute(select(GentleWord).where(GentleWord.id == gentle_word_id).with_for_update())
        ).scalar_one_or_none()
        if not gw or gw.status == GentleWordStatus.DELETED.value:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gentle Word not found.")

        if gw.status == GentleWordStatus.REJECTED.value:
            return gw

        if gw.status != GentleWordStatus.PENDING.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Approved Gentle Words cannot be rejected directly; hide content and reverse points if needed.",
            )

        now = datetime.now(timezone.utc)
        prev_status = gw.status
        gw.status = GentleWordStatus.REJECTED.value
        gw.moderated_by = admin_id
        gw.moderated_at = now
        gw.moderation_note = note or reason
        gw.points_awarded = 0
        gw.updated_at = now
        session.add(gw)

        await self._write_audit_log(
            session=session,
            admin_id=admin_id,
            action="gentle_word_reject",
            target_type="gentle_word",
            target_id=gw.id,
            previous_status=prev_status,
            new_status=gw.status,
            reason=reason or note,
        )

        await self._send_notification_safe(
            session=session,
            user_id=gw.author_user_id,
            notification_type=NotificationType.GENTLE_WORD_REJECTED.value,
            title="Update on your Gentle Word",
            content="Your Gentle Word wasn't approved this time. You can review the community guidelines and try again.",
            reference_id=gw.id,
            reference_type="gentle_word",
        )

        await session.commit()
        await session.refresh(gw)
        return gw

    async def hide_content(
        self,
        session: AsyncSession,
        admin_id: UUID,
        target_type: ReportTargetType,
        target_id: UUID,
        reason: str,
        reverse_points: bool = False,
        auto_commit: bool = True,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)

        if target_type == ReportTargetType.THOUGHT:
            thought = (
                await session.execute(select(Thought).where(Thought.id == target_id).with_for_update())
            ).scalar_one_or_none()
            if not thought or thought.status == ThoughtStatus.DELETED.value:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thought not found.")

            prev_status = thought.status
            if prev_status != ThoughtStatus.HIDDEN.value:
                thought.status = ThoughtStatus.HIDDEN.value
                thought.moderated_by = admin_id
                thought.moderated_at = now
                thought.moderation_note = reason
                thought.updated_at = now
                session.add(thought)

                await self._write_audit_log(
                    session=session,
                    admin_id=admin_id,
                    action="content_hide",
                    target_type="thought",
                    target_id=thought.id,
                    previous_status=prev_status,
                    new_status=thought.status,
                    reason=reason,
                )
                await self._send_notification_safe(
                    session=session,
                    user_id=thought.author_user_id,
                    notification_type=NotificationType.CONTENT_HIDDEN.value,
                    title="Your Thought was hidden",
                    content="One of your Thoughts in The Listening Room was hidden following moderation review.",
                    reference_id=thought.id,
                    reference_type="thought",
                )

            if auto_commit:
                await session.commit()
            return {"target_type": "thought", "target_id": thought.id, "status": thought.status}

        # Gentle Word hide
        gw = (
            await session.execute(select(GentleWord).where(GentleWord.id == target_id).with_for_update())
        ).scalar_one_or_none()
        if not gw or gw.status == GentleWordStatus.DELETED.value:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gentle Word not found.")

        prev_status = gw.status
        if prev_status != GentleWordStatus.HIDDEN.value:
            if prev_status == GentleWordStatus.APPROVED.value:
                parent_thought = (
                    await session.execute(select(Thought).where(Thought.id == gw.thought_id).with_for_update())
                ).scalar_one_or_none()
                if parent_thought and parent_thought.gentle_words_count > 0:
                    parent_thought.gentle_words_count -= 1
                    parent_thought.updated_at = now
                    session.add(parent_thought)

            gw.status = GentleWordStatus.HIDDEN.value
            gw.moderated_by = admin_id
            gw.moderated_at = now
            gw.moderation_note = reason
            gw.updated_at = now
            session.add(gw)

            reversed_pts = 0
            if reverse_points and gw.points_awarded > 0:
                _, _, created_rev = await self._append_ledger_entry(
                    session=session,
                    user_id=gw.author_user_id,
                    entry_type=LedgerEntryType.REVERSAL.value,
                    points_delta=-gw.points_awarded,
                    source_type="gentle_word",
                    source_id=gw.id,
                    idempotency_key=f"gw_reverse:{gw.id}",
                    description=f"Points reversed: {reason}",
                    admin_id=admin_id,
                    allow_negative_balance=True,
                )
                if created_rev:
                    reversed_pts = gw.points_awarded

            await self._write_audit_log(
                session=session,
                admin_id=admin_id,
                action="content_hide",
                target_type="gentle_word",
                target_id=gw.id,
                previous_status=prev_status,
                new_status=gw.status,
                reason=reason,
                metadata={"points_reversed": reversed_pts},
            )
            await self._send_notification_safe(
                session=session,
                user_id=gw.author_user_id,
                notification_type=NotificationType.CONTENT_HIDDEN.value,
                title="Your Gentle Word was hidden",
                content="One of your Gentle Words was hidden following moderation review.",
                reference_id=gw.id,
                reference_type="gentle_word",
            )

        if auto_commit:
            await session.commit()
        return {"target_type": "gentle_word", "target_id": gw.id, "status": gw.status}

    async def list_reports(
        self,
        session: AsyncSession,
        report_status: Optional[str] = ReportStatus.PENDING.value,
        skip: int = 0,
        limit: int = 50,
    ) -> Dict[str, Any]:
        filters = []
        if report_status:
            filters.append(ContentReport.status == report_status)

        total = (
            await session.execute(select(func.count(ContentReport.id)).where(*filters))
        ).scalar() or 0

        stmt = (
            select(ContentReport)
            .where(*filters)
            .order_by(ContentReport.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
        reports = list((await session.execute(stmt)).scalars().all())

        thought_ids = [r.target_id for r in reports if r.target_type == ReportTargetType.THOUGHT.value]
        gw_ids = [r.target_id for r in reports if r.target_type == ReportTargetType.GENTLE_WORD.value]

        previews: Dict[UUID, str] = {}
        if thought_ids:
            t_rows = (await session.execute(select(Thought.id, Thought.body).where(Thought.id.in_(thought_ids)))).all()
            for tid, body in t_rows:
                previews[tid] = (body[:160] + "...") if len(body) > 160 else body
        if gw_ids:
            g_rows = (await session.execute(select(GentleWord.id, GentleWord.body).where(GentleWord.id.in_(gw_ids)))).all()
            for gid, body in g_rows:
                previews[gid] = (body[:160] + "...") if len(body) > 160 else body

        items = [
            {
                "id": r.id,
                "reporter_user_id": r.reporter_user_id,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "target_preview": previews.get(r.target_id),
                "reason": r.reason,
                "details": r.details,
                "status": r.status,
                "reviewed_by": r.reviewed_by,
                "resolution_note": r.resolution_note,
                "reviewed_at": r.reviewed_at,
                "created_at": r.created_at,
            }
            for r in reports
        ]
        return {"total": int(total), "items": items}

    async def resolve_report(
        self,
        session: AsyncSession,
        admin_id: UUID,
        report_id: UUID,
        new_status: ReportStatus,
        note: Optional[str] = None,
        hide_target_content: bool = False,
        reverse_points: bool = False,
    ) -> ContentReport:
        report = (
            await session.execute(select(ContentReport).where(ContentReport.id == report_id).with_for_update())
        ).scalar_one_or_none()
        if not report:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content report not found.")

        now = datetime.now(timezone.utc)
        prev_status = report.status
        report.status = new_status.value
        report.reviewed_by = admin_id
        report.reviewed_at = now
        report.resolution_note = note
        session.add(report)

        if hide_target_content:
            target_enum = ReportTargetType(report.target_type)
            await self.hide_content(
                session=session,
                admin_id=admin_id,
                target_type=target_enum,
                target_id=report.target_id,
                reason=note or f"Actioned via report {report.id}",
                reverse_points=reverse_points,
                auto_commit=False,
            )

        await self._write_audit_log(
            session=session,
            admin_id=admin_id,
            action="report_resolve",
            target_type="content_report",
            target_id=report.id,
            previous_status=prev_status,
            new_status=report.status,
            reason=note,
            metadata={"hide_target_content": hide_target_content, "reverse_points": reverse_points},
        )
        await session.commit()
        await session.refresh(report)
        return report

    async def set_user_posting_restriction(
        self,
        session: AsyncSession,
        admin_id: UUID,
        user_id: UUID,
        restricted: bool,
        reason: str,
        duration_hours: Optional[int] = 24,
    ) -> Dict[str, Any]:
        bal = await self._get_or_create_balance_locked(session, user_id)
        now = datetime.now(timezone.utc)
        prev_val = str(bal.posting_restricted_until) if bal.posting_restricted_until else "unrestricted"

        if restricted:
            hours = duration_hours or 24
            bal.posting_restricted_until = now + timedelta(hours=hours)
            bal.posting_restriction_reason = reason
            bal.restricted_by_admin_id = admin_id
            new_val = str(bal.posting_restricted_until)
        else:
            bal.posting_restricted_until = None
            bal.posting_restriction_reason = reason
            bal.restricted_by_admin_id = admin_id
            new_val = "unrestricted"

        bal.updated_at = now
        session.add(bal)

        await self._write_audit_log(
            session=session,
            admin_id=admin_id,
            action="user_posting_restriction",
            target_type="user",
            target_id=user_id,
            previous_status=prev_val,
            new_status=new_val,
            reason=reason,
        )
        await session.commit()
        return {
            "user_id": user_id,
            "restricted": restricted,
            "posting_restricted_until": bal.posting_restricted_until,
            "reason": reason,
        }

    async def adjust_kindness_points(
        self,
        session: AsyncSession,
        admin_id: UUID,
        user_id: UUID,
        points_delta: int,
        reason: str,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        entry_type = (
            LedgerEntryType.ADMIN_ADJUSTMENT.value
            if points_delta > 0
            else LedgerEntryType.REVERSAL.value
        )
        entry, bal, created_new = await self._append_ledger_entry(
            session=session,
            user_id=user_id,
            entry_type=entry_type,
            points_delta=points_delta,
            source_type="admin_action",
            source_id=admin_id,
            idempotency_key=f"admin_adj:{idempotency_key}",
            description=reason,
            admin_id=admin_id,
            allow_negative_balance=True,
        )

        if created_new:
            await self._write_audit_log(
                session=session,
                admin_id=admin_id,
                action="kindness_points_adjust",
                target_type="user",
                target_id=user_id,
                reason=reason,
                metadata={"points_delta": points_delta, "balance_after": bal.balance},
            )
            await session.commit()

        return {
            "id": entry.id,
            "type": entry.entry_type,
            "points_delta": entry.points_delta,
            "balance_after": entry.balance_after,
            "source_type": entry.source_type,
            "description": entry.description,
            "created_at": entry.created_at,
        }

    async def create_little_perk(
        self,
        session: AsyncSession,
        admin_id: UUID,
        data: Dict[str, Any],
    ) -> LittlePerk:
        now = datetime.now(timezone.utc)
        perk = LittlePerk(
            name=data["name"],
            description=data["description"],
            points_cost=data["points_cost"],
            discount_type=data["discount_type"].value if hasattr(data["discount_type"], "value") else data["discount_type"],
            discount_value=data["discount_value"],
            currency=data.get("currency", "NGN"),
            eligible_celebration_types=data.get("eligible_celebration_types", []),
            minimum_order_amount=data.get("minimum_order_amount"),
            maximum_discount_amount=data.get("maximum_discount_amount"),
            starts_at=data.get("starts_at"),
            ends_at=data.get("ends_at"),
            active=data.get("active", True),
            usage_limit_total=data.get("usage_limit_total"),
            usage_limit_per_user=data.get("usage_limit_per_user"),
            created_by=admin_id,
            created_at=now,
            updated_at=now,
        )
        session.add(perk)
        await session.flush()
        await self._write_audit_log(
            session=session,
            admin_id=admin_id,
            action="little_perk_create",
            target_type="little_perk",
            target_id=perk.id,
            new_status="active" if perk.active else "inactive",
        )
        await session.commit()
        await session.refresh(perk)
        return perk

    async def update_little_perk(
        self,
        session: AsyncSession,
        admin_id: UUID,
        perk_id: UUID,
        updates: Dict[str, Any],
    ) -> LittlePerk:
        perk = (
            await session.execute(select(LittlePerk).where(LittlePerk.id == perk_id).with_for_update())
        ).scalar_one_or_none()
        if not perk:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Little Perk not found.")

        for field, val in updates.items():
            if val is not None:
                setattr(perk, field, val)
        perk.updated_at = datetime.now(timezone.utc)
        session.add(perk)

        await self._write_audit_log(
            session=session,
            admin_id=admin_id,
            action="little_perk_update",
            target_type="little_perk",
            target_id=perk.id,
            new_status="active" if perk.active else "inactive",
        )
        await session.commit()
        await session.refresh(perk)
        return perk

    async def update_points_policy(
        self,
        session: AsyncSession,
        admin_id: UUID,
        updates: Dict[str, Any],
    ) -> KindnessPointsPolicy:
        policy = await self.get_or_create_policy(session)
        for field, val in updates.items():
            if val is not None:
                setattr(policy, field, val)
        policy.updated_by_admin_id = admin_id
        policy.updated_at = datetime.now(timezone.utc)
        session.add(policy)

        await self._write_audit_log(
            session=session,
            admin_id=admin_id,
            action="points_policy_update",
            target_type="kindness_points_policy",
            target_id=UUID(int=1),
            metadata=updates,
        )
        await session.commit()
        await session.refresh(policy)
        return policy

    async def get_listening_room_analytics(self, session: AsyncSession) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)

        # Contributors (distinct authors of thoughts or gentle words)
        wac_t = set(
            (await session.execute(select(Thought.author_user_id).where(Thought.created_at >= week_ago).distinct())).scalars().all()
        )
        wac_gw = set(
            (await session.execute(select(GentleWord.author_user_id).where(GentleWord.created_at >= week_ago).distinct())).scalars().all()
        )
        mac_t = set(
            (await session.execute(select(Thought.author_user_id).where(Thought.created_at >= month_ago).distinct())).scalars().all()
        )
        mac_gw = set(
            (await session.execute(select(GentleWord.author_user_id).where(GentleWord.created_at >= month_ago).distinct())).scalars().all()
        )

        # Thoughts counts
        t_row = (
            await session.execute(
                select(
                    func.count(Thought.id).label("total"),
                    func.count(case((Thought.status == ThoughtStatus.PUBLISHED.value, Thought.id))).label("published"),
                    func.count(case((Thought.status == ThoughtStatus.PENDING.value, Thought.id))).label("pending"),
                    func.count(case((Thought.status == ThoughtStatus.HIDDEN.value, Thought.id))).label("hidden"),
                    func.count(case((Thought.status == ThoughtStatus.REJECTED.value, Thought.id))).label("rejected"),
                ).where(Thought.deleted_at.is_(None))
            )
        ).first()

        thoughts_reported = (
            await session.execute(
                select(func.count(func.distinct(ContentReport.target_id))).where(
                    ContentReport.target_type == ReportTargetType.THOUGHT.value
                )
            )
        ).scalar() or 0

        # Gentle Words counts
        gw_row = (
            await session.execute(
                select(
                    func.count(GentleWord.id).label("total"),
                    func.count(case((GentleWord.status == GentleWordStatus.APPROVED.value, GentleWord.id))).label("approved"),
                    func.count(case((GentleWord.status == GentleWordStatus.REJECTED.value, GentleWord.id))).label("rejected"),
                    func.count(case((GentleWord.status == GentleWordStatus.PENDING.value, GentleWord.id))).label("pending"),
                ).where(GentleWord.deleted_at.is_(None))
            )
        ).first()

        gw_total = int(gw_row.total or 0)
        gw_approved = int(gw_row.approved or 0)
        gw_rejected = int(gw_row.rejected or 0)
        gw_decided = gw_approved + gw_rejected

        # Median moderation time (for recently moderated gentle words)
        mod_times = (
            await session.execute(
                select(GentleWord.created_at, GentleWord.moderated_at)
                .where(GentleWord.moderated_at.isnot(None))
                .order_by(GentleWord.moderated_at.desc())
                .limit(500)
            )
        ).all()
        durations = [
            max(0.0, (m_at - c_at).total_seconds())
            for c_at, m_at in mod_times
            if m_at and c_at
        ]
        median_mod_sec = round(statistics.median(durations), 2) if durations else None

        # Points metrics
        pts_row = (
            await session.execute(
                select(
                    func.coalesce(func.sum(case((KindnessPointsLedger.entry_type == LedgerEntryType.AWARD.value, KindnessPointsLedger.points_delta), else_=0)), 0).label("awarded"),
                    func.coalesce(func.sum(case((KindnessPointsLedger.entry_type == LedgerEntryType.REDEMPTION.value, func.abs(KindnessPointsLedger.points_delta)), else_=0)), 0).label("redeemed"),
                    func.coalesce(func.sum(case((KindnessPointsLedger.entry_type == LedgerEntryType.REVERSAL.value, func.abs(KindnessPointsLedger.points_delta)), else_=0)), 0).label("reversed"),
                    func.coalesce(func.sum(KindnessPointsLedger.points_delta), 0).label("outstanding"),
                )
            )
        ).first()

        # Perk redemptions
        red_row = (
            await session.execute(
                select(
                    func.count(PerkRedemption.id).label("total"),
                    func.count(case((PerkRedemption.status == RedemptionStatus.USED.value, PerkRedemption.id))).label("used"),
                )
            )
        ).first()
        red_total = int(red_row.total or 0)
        red_used = int(red_row.used or 0)

        # Reports
        rep_pending = (
            await session.execute(
                select(func.count(ContentReport.id)).where(ContentReport.status == ReportStatus.PENDING.value)
            )
        ).scalar() or 0
        rep_total = (await session.execute(select(func.count(ContentReport.id)))).scalar() or 0
        total_posts = int(t_row.total or 0) + gw_total

        return {
            "weekly_active_contributors": len(wac_t | wac_gw),
            "monthly_active_contributors": len(mac_t | mac_gw),
            "thoughts_total": int(t_row.total or 0),
            "thoughts_published": int(t_row.published or 0),
            "thoughts_pending": int(t_row.pending or 0),
            "thoughts_hidden": int(t_row.hidden or 0),
            "thoughts_rejected": int(t_row.rejected or 0),
            "thoughts_reported": int(thoughts_reported),
            "gentle_words_submitted": gw_total,
            "gentle_words_approved": gw_approved,
            "gentle_words_rejected": gw_rejected,
            "gentle_words_pending": int(gw_row.pending or 0),
            "gentle_words_approval_rate_pct": round((gw_approved / gw_decided) * 100, 2) if gw_decided > 0 else 0.0,
            "gentle_words_rejection_rate_pct": round((gw_rejected / gw_decided) * 100, 2) if gw_decided > 0 else 0.0,
            "median_moderation_seconds": median_mod_sec,
            "points_awarded_total": int(pts_row.awarded or 0),
            "points_redeemed_total": int(pts_row.redeemed or 0),
            "points_reversed_total": int(pts_row.reversed or 0),
            "points_outstanding_total": int(pts_row.outstanding or 0),
            "perk_redemptions_total": red_total,
            "perk_redemptions_used": red_used,
            "perk_checkout_conversion_pct": round((red_used / red_total) * 100, 2) if red_total > 0 else 0.0,
            "reports_pending": int(rep_pending),
            "reports_per_100_posts": round((int(rep_total) / total_posts) * 100, 2) if total_posts > 0 else 0.0,
        }


listening_room_service = ListeningRoomService()
