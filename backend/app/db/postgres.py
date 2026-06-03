"""SQLAlchemy 엔진 및 세션 팩토리.

async 엔진 (asyncpg)  → FastAPI 라우터 / timeline CRUD
sync  엔진 (psycopg2) → alembic 마이그레이션 / 캐시 헬퍼(dynamodb.py)
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# backend/.env 를 파일 위치 기준 절대 경로로 로드 — CWD 에 무관하게 동작
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ENV_PATH, override=False)

_DB_URL      = os.environ.get("DATABASE_URL", "")
_DB_URL_SYNC = os.environ.get("DATABASE_URL_SYNC", "")

if not _DB_URL:
    raise RuntimeError(
        f".env 파일({_ENV_PATH})에 DATABASE_URL 이 없습니다.\n"
        "예시: DATABASE_URL=postgresql+asyncpg://postgres:postgres@<host>:5432/<db>"
    )

# ── 비동기 (FastAPI / timeline CRUD) ─────────────────────────────────────────
async_engine      = create_async_engine(_DB_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)

# ── 동기 (캐시 헬퍼 / alembic) ───────────────────────────────────────────────
sync_engine      = create_engine(_DB_URL_SYNC or _DB_URL.replace("+asyncpg", "+psycopg2"), pool_pre_ping=True)
SyncSessionLocal = sessionmaker(sync_engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI Depends 주입용 비동기 세션."""
    async with AsyncSessionLocal() as session:
        yield session
