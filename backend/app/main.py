"""CodeWhy 백엔드 진입점."""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.features.blame.router import router as blame_router
from app.features.timeline.router import router as timeline_router
from app.features.traceability.router import router as traceability_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.db.postgres import engine, Base
    import app.db.models  # noqa: F401

    # RDS 연결 확인
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("PostgreSQL 연결 성공")
    except Exception as e:
        logger.warning("PostgreSQL 연결 실패 (Timeline 기능 비활성화): %s", e)

    # AUTO_CREATE_TABLES=true 일 때 테이블 자동 생성 (로컬 개발 전용)
    if os.getenv("AUTO_CREATE_TABLES", "false").lower() == "true":
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("테이블 자동 생성 완료")
        except Exception as e:
            logger.warning("테이블 자동 생성 실패: %s", e)

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

app.include_router(blame_router,       prefix="/api/blame",    tags=["Context Blame"])
app.include_router(timeline_router,    prefix="/api/timeline", tags=["Timeline Summary"])
app.include_router(traceability_router, prefix="/api/trace",   tags=["Requirement Trace"])


@app.get("/health")
def health():
    return {"status": "ok"}
