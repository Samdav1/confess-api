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
