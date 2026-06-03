"""CodeWhy 백엔드 진입점."""

import logging
from contextlib import asynccontextmanager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.features.blame.router import router as blame_router
from app.features.timeline.router import router as timeline_router
from app.features.traceability.router import router as traceability_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── PostgreSQL 연결 확인 + 테이블 자동 생성 ───────────────────────────────
    from app.db.postgres import async_engine, Base
    import app.db.models  # noqa: F401 — Base.metadata 에 모델 등록

    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("PostgreSQL 연결 성공")

        # 테이블이 없으면 자동 생성 (운영에서는 alembic 사용 권장)
        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("테이블 준비 완료")

    except Exception as e:
        logger.warning("PostgreSQL 연결 실패: %s", e)

    yield


app = FastAPI(
    title="CodeWhy Backend",
    description="컨텍스트 블레임 / 타임라인 요약 / 요구사항 역추적 API",
    version="0.0.1",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(blame_router,        prefix="/api/blame",    tags=["Context Blame"])
app.include_router(timeline_router,     prefix="/api/timeline", tags=["Timeline Summary"])
app.include_router(traceability_router, prefix="/api/trace",    tags=["Requirement Trace"])


@app.get("/health")
def health():
    return {"status": "ok"}
