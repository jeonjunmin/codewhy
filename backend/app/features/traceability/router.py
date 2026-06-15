"""Requirement Trace API 라우터.

POST /api/trace/requirement — 코드 라인에서 연관 GitHub Issue 기획 문서를 찾아 반환한다.

DB 의존 없음 — GitHub API 실시간 조회로 동작.

👤 담당: 개발자 C
"""

import logging

from fastapi import APIRouter, HTTPException

from app.ai.trace_graph import atrace
from app.features.traceability.schemas import (
    DocumentMatch,
    TraceRequest,
    TraceResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/requirement", response_model=TraceResponse)
async def requirement_trace(req: TraceRequest):
    try:
        # 폴백 체인을 trace_graph(LangGraph)로 실행. 노드 내부에서 git/GitHub 블로킹 호출을
        # asyncio.to_thread 로 위임하므로 이벤트 루프를 점유하지 않는다.
        matches = await atrace(req.repoPath, req.filePath, req.line)
    except Exception as e:
        logger.exception(
            "requirement trace 실패 — repo=%s file=%s line=%s",
            req.repoPath, req.filePath, req.line,
        )
        raise HTTPException(status_code=500, detail=f"requirement trace 실패: {e}")
    return TraceResponse(documents=[DocumentMatch(**m) for m in matches])
