"""CodeWhy 백엔드 진입점."""

import logging
from contextlib import asynccontextmanager

import boto3

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.dynamo_session import get_client_kwargs
from app.features.blame.router import router as blame_router
from app.features.timeline.router import router as timeline_router
from app.features.traceability.router import router as traceability_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── DynamoDB 연결 확인 (startup) ──────────────────────────────────────────
    try:
        boto3.client("dynamodb", **get_client_kwargs()).describe_limits()
        logger.info("DynamoDB 연결 성공")
    except Exception as e:
        logger.warning("DynamoDB 연결 실패 — 로컬 Docker가 실행 중인지 확인하세요: %s", e)

    yield  # ── 서버 실행 중 ───────────────────────────────────────────────────


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
