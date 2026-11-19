from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from app.config.settings import settings

engine = create_async_engine(
    url=settings.DATABASE_URL,
    echo=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,
)

async def get_session() -> AsyncSession:
    async with AsyncSession(engine) as session:
        yield session

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        if conn.dialect.name == "postgresql":
            await conn.execute(text("""
                DO $$
                DECLARE
                    col RECORD;
                BEGIN
                    FOR col IN
                        SELECT table_name, column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND (
                              (table_name = 'anonymous_links' AND column_name IN ('expires_at', 'created_at'))
                              OR (table_name = 'anonymous_messages' AND column_name IN ('created_at'))
                              OR (table_name = 'payments' AND column_name IN ('paid_at', 'created_at', 'updated_at'))
                          )
                          AND data_type = 'timestamp without time zone'
                    LOOP
                        EXECUTE format(
                            'ALTER TABLE %I ALTER COLUMN %I TYPE TIMESTAMP WITH TIME ZONE USING %I AT TIME ZONE ''UTC''',
                            col.table_name, col.column_name, col.column_name
                        );
                    END LOOP;
                END $$;
            """))
            await conn.execute(text("ALTER TABLE listening_room_thoughts ADD COLUMN IF NOT EXISTS author_label VARCHAR NOT NULL DEFAULT 'A quiet heart';"))
            await conn.execute(text("ALTER TABLE listening_room_thoughts ADD COLUMN IF NOT EXISTS tone VARCHAR NOT NULL DEFAULT 'cream';"))
            await conn.execute(text("ALTER TABLE listening_room_thoughts ADD COLUMN IF NOT EXISTS content_hash VARCHAR;"))
            await conn.execute(text("ALTER TABLE listening_room_thoughts ADD COLUMN IF NOT EXISTS likes_count INTEGER NOT NULL DEFAULT 0;"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_lr_thoughts_content_hash ON listening_room_thoughts (content_hash);"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_lr_thoughts_status_created ON listening_room_thoughts (status, created_at);"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_lr_thoughts_cat_status_created ON listening_room_thoughts (category, status, created_at);"))
            await conn.execute(text("ALTER TABLE listening_room_gentle_words ADD COLUMN IF NOT EXISTS author_label VARCHAR NOT NULL DEFAULT 'A kind stranger';"))
            await conn.execute(text("ALTER TABLE listening_room_gentle_words ADD COLUMN IF NOT EXISTS content_hash VARCHAR;"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_lr_gw_content_hash ON listening_room_gentle_words (content_hash);"))
            await conn.execute(text("ALTER TABLE listening_room_thoughts ADD COLUMN IF NOT EXISTS approved_at TIMESTAMP WITH TIME ZONE;"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_lr_thoughts_status_approved ON listening_room_thoughts (status, approved_at, id);"))
            await conn.execute(text("UPDATE listening_room_thoughts SET approved_at = COALESCE(moderated_at, published_at, created_at) WHERE status = 'published' AND approved_at IS NULL;"))

    await _seed_listening_room_defaults()


async def _seed_listening_room_defaults():
    from sqlalchemy import select, func
    from app.models.listening_room import DiscountType, KindnessPointsPolicy, LittlePerk

    async with AsyncSession(engine) as session:
        policy = (
            await session.execute(select(KindnessPointsPolicy).where(KindnessPointsPolicy.id == 1))
        ).scalar_one_or_none()
        if not policy:
            session.add(KindnessPointsPolicy(id=1))

        perk_count = (await session.execute(select(func.count(LittlePerk.id)))).scalar() or 0
        if perk_count == 0:
            session.add(
                LittlePerk(
                    name="10% Off Celebration Page",
                    description="Redeem 100 Kindness Points for 10% off any Confess Celebration page (up to ₦500).",
                    points_cost=100,
                    discount_type=DiscountType.PERCENTAGE.value,
                    discount_value=10.0,
                    currency="NGN",
                    eligible_celebration_types=[],
                    minimum_order_amount=500.0,
                    maximum_discount_amount=500.0,
                    active=True,
                )
            )
            session.add(
                LittlePerk(
                    name="₦500 Off Celebration Page",
                    description="Redeem 250 Kindness Points for ₦500 off a Celebration page of ₦1,000 or more.",
                    points_cost=250,
                    discount_type=DiscountType.FIXED_AMOUNT.value,
                    discount_value=500.0,
                    currency="NGN",
                    eligible_celebration_types=[],
                    minimum_order_amount=1000.0,
                    maximum_discount_amount=500.0,
                    active=True,
                )
            )

        await session.commit()
