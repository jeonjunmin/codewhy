"""Requirement Trace API 라우터.

POST /api/trace/requirement — 코드 라인에서 연관 기획 문서(다운로드 URL 포함)를 찾아 반환한다.

👤 담당: 개발자 C
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import get_db
from app.features.traceability import service
from app.features.traceability.schemas import (
    DocumentMatch,
    TraceRequest,
    TraceResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/requirement", response_model=TraceResponse)
async def requirement_trace(req: TraceRequest, db: AsyncSession = Depends(get_db)):
    try:
        matches = await service.trace(db, req.repoPath, req.filePath, req.line)
    except Exception as e:
        logger.exception("requirement trace 실패 — repo=%s file=%s line=%s", req.repoPath, req.filePath, req.line)
        raise HTTPException(status_code=500, detail=f"requirement trace 실패: {e}")
    return TraceResponse(documents=[DocumentMatch(**m) for m in matches])
