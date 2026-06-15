"""Requirement Trace API 라우터.

POST /api/trace/requirement — 코드 라인에서 연관 GitHub Issue 기획 문서를 찾아 반환한다.

DB 의존 없음 — GitHub API 실시간 조회로 동작.

👤 담당: 개발자 C
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from app.core import vcs
from app.features.traceability import service
from app.features.traceability.schemas import (
    DocumentMatch,
    TraceRequest,
    TraceResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/requirement", response_model=TraceResponse)
async def requirement_trace(req: TraceRequest):
    # blamed 커밋이 없으면(미커밋 라인 등) 연관 문서를 찾을 대상이 없다.
    if req.blame is None:
        return TraceResponse(documents=[])

    remote = vcs.parse_remote(req.remoteUrl)
    try:
        # GitHub API 동기 블로킹 → 이벤트 루프 보호
        matches = await asyncio.to_thread(
            service.trace, req.blame.commitHash, req.blame.message,
            branch=req.branch, remote=remote,
        )
    except Exception as e:
        logger.exception(
            "requirement trace 실패 — repo=%s file=%s line=%s",
            req.repoPath, req.filePath, req.line,
        )
        raise HTTPException(status_code=500, detail=f"requirement trace 실패: {e}")
    return TraceResponse(documents=[DocumentMatch(**m) for m in matches])
