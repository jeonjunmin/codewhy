"""Requirement Trace API 라우터.

POST /api/trace/requirement — 코드에서 연관 기획 문서를 찾아 반환한다.
DOCUMENT_PATHS 환경변수가 비어있으면 400 을 반환한다.

👤 담당: 개발자 C
"""

from fastapi import APIRouter, HTTPException

from app.core.config import get_document_paths
from app.features.traceability import service
from app.features.traceability.schemas import (
    DocumentMatch,
    TraceRequest,
    TraceResponse,
)

router = APIRouter()


@router.post("/requirement", response_model=TraceResponse)
def requirement_trace(req: TraceRequest):
    document_paths = get_document_paths()
    if not document_paths:
        raise HTTPException(
            status_code=400,
            detail="DOCUMENT_PATHS 환경변수가 설정되지 않았습니다.",
        )

    matches = service.trace(req.repoPath, req.filePath, req.line, document_paths)
    return TraceResponse(documents=[DocumentMatch(**m) for m in matches])
