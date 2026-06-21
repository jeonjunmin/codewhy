"""PostgreSQL(RDS) 연결 레이어 — SQLAlchemy 2.0 async.

세 기능(블레임/타임라인/역추적)이 공유하는 정규화 스키마의 DB 세션을 제공한다.

- 런타임(FastAPI): asyncpg 드라이버로 비동기 접속 (`DATABASE_URL`)
- Alembic 마이그레이션: psycopg2 동기 드라이버 (`get_rds_url_sync`) — env.py 전용

자격증명은 환경변수(.env)의 DATABASE_URL 에서 읽는다 — 코드에 비밀번호 없음.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator, Coroutine
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_rds_url_async

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """모든 ORM 모델의 공통 베이스. Alembic 이 Base.metadata 로 스키마를 추적한다."""


# 앱 전역 싱글턴 엔진/세션 팩토리 (요청마다 재생성하지 않는다)
engine = create_async_engine(
    get_rds_url_async(),
    pool_pre_ping=True,   # 유휴 커넥션 끊김(RDS idle timeout) 자동 복구
    # 커넥션을 5분마다 선제적으로 재활용한다. RDS/네트워크(AWS NLB ~350초) 유휴 타임아웃에
    # 커넥션이 끊긴 채 풀에 남는 것을 줄여, 끊긴 커넥션을 폐기(terminate)하는 경로 진입을
    # 최소화한다 — 그 폐기 경로의 graceful-close await 가 Python 3.14 에서 깨지는 noise 의 원인이다.
    pool_recycle=300,
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


# 진행 중 백그라운드 태스크를 붙들어 GC 로 중간 취소되지 않게 한다(완료 시 자동 제거).
_background_tasks: set[asyncio.Task] = set()


def run_detached(coro: Coroutine[Any, Any, Any]) -> None:
    """코루틴을 요청 취소 범위 '밖'의 독립 asyncio 태스크로 실행한다(fire-and-forget).

    스트리밍 응답이 끝날 때 클라이언트가 연결을 끊으면 starlette(anyio) 가 요청 태스크그룹의
    취소 스코프를 취소한다. 캐시 저장 같은 뒷작업을 그 스코프 안에서 await 하면, 진행 중이던
    asyncpg 커넥션이 취소에 휩쓸려 terminate 되고 `Exception terminating connection`(CancelledError)
    noise 가 난다. anyio 취소 스코프는 자기 태스크그룹만 취소하므로, asyncio.ensure_future 로
    직접 띄운 태스크는 거기서 벗어나 안전하게 완료된다.
    """
    task = asyncio.ensure_future(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
