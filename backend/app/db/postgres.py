"""PostgreSQL(RDS) 연결 레이어 — SQLAlchemy 2.0 async.

세 기능(블레임/타임라인/역추적)이 공유하는 정규화 스키마의 DB 세션을 제공한다.

- 런타임(FastAPI): asyncpg 드라이버로 비동기 접속 (`DATABASE_URL`)
- Alembic 마이그레이션: psycopg2 동기 드라이버 (`get_rds_url_sync`) — env.py 전용

자격증명은 환경변수(.env)의 DATABASE_URL 에서 읽는다 — 코드에 비밀번호 없음.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_rds_url_async


class Base(DeclarativeBase):
    """모든 ORM 모델의 공통 베이스. Alembic 이 Base.metadata 로 스키마를 추적한다."""


# 앱 전역 싱글턴 엔진/세션 팩토리 (요청마다 재생성하지 않는다)
engine = create_async_engine(
    get_rds_url_async(),
    pool_pre_ping=True,   # 유휴 커넥션 끊김(RDS idle timeout) 자동 복구
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,   # 커밋 후에도 ORM 객체 속성 접근 가능
    class_=AsyncSession,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 의존성 — 요청 단위 AsyncSession 을 열고 자동으로 닫는다."""
    async with AsyncSessionLocal() as session:
        yield session
