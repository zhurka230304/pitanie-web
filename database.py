from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:password@localhost/pitanie")
# Railway sometimes gives postgres:// instead of postgresql+asyncpg://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Safe column migrations for existing tables
        for stmt in [
            "ALTER TABLE trainer_clients ADD COLUMN IF NOT EXISTS invite_token VARCHAR(64)",
            "ALTER TABLE client_profiles ADD COLUMN IF NOT EXISTS birth_date DATE",
            "ALTER TABLE client_profiles ADD COLUMN IF NOT EXISTS weight_kg FLOAT",
            "ALTER TABLE client_profiles ADD COLUMN IF NOT EXISTS height_cm FLOAT",
            "ALTER TABLE client_profiles ADD COLUMN IF NOT EXISTS sex VARCHAR(10)",
            "ALTER TABLE client_profiles ADD COLUMN IF NOT EXISTS activity FLOAT",
            "ALTER TABLE client_profiles ADD COLUMN IF NOT EXISTS goal_formula VARCHAR(20)",
            "ALTER TABLE client_invites ADD COLUMN IF NOT EXISTS birth_date DATE",
            "ALTER TABLE selfserve_accounts ADD COLUMN IF NOT EXISTS tracking JSON",
        ]:
            await conn.execute(text(stmt))
