"""SQLAlchemy 비동기 엔진 및 세션 팩토리.

앱 코드 → AsyncSessionLocal (asyncpg)
alembic  → get_rds_url_sync() (psycopg2) — alembic/env.py 에서 직접 사용
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_rds_url

engine = create_async_engine(get_rds_url(), echo=False, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI Depends 주입용 세션 제공자."""
    async with AsyncSessionLocal() as session:
        yield session
