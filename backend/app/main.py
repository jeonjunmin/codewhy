"""CodeWhy 백엔드 진입점."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.db.postgres import engine
from app.features.blame.router import router as blame_router
from app.features.documents.router import router as documents_router
from app.features.timeline.router import router as timeline_router
from app.features.traceability.router import router as traceability_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── PostgreSQL(RDS) 연결 확인 (startup) ───────────────────────────────────
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("PostgreSQL 연결 성공")
    except Exception as e:
        logger.warning("PostgreSQL 연결 실패 — DATABASE_URL / DB 기동 여부를 확인하세요: %s", e)

    yield  # ── 서버 실행 중 ───────────────────────────────────────────────────

    await engine.dispose()


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

app.include_router(blame_router,        prefix="/api/blame",     tags=["Context Blame"])
app.include_router(timeline_router,     prefix="/api/timeline",  tags=["Timeline Summary"])
app.include_router(traceability_router, prefix="/api/trace",     tags=["Requirement Trace"])
app.include_router(documents_router,    prefix="/api/documents", tags=["Documents"])


@app.get("/health")
def health():
    return {"status": "ok"}
