"""Context Blame API 라우터.

POST /api/blame/context — 한 라인의 변경 사유를 분석해 반환한다.
DynamoDB 캐시가 있으면 그대로 돌려준다.

👤 담당: 개발자 A
"""

import logging

from fastapi import APIRouter, HTTPException

from app.db import dynamodb
from app.features.blame import service
from app.features.blame.schemas import BlameRequest, BlameResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/context", response_model=BlameResponse)
def context_blame(req: BlameRequest):
    try:
        cached = dynamodb.get_blame_cache(req.repoPath, req.filePath, req.line)
        if cached:
            return BlameResponse(**cached)
    except Exception:
        pass  # DynamoDB 미설정 환경(로컬 개발)에서도 분석은 계속 진행

    try:
        result = service.analyze_blame(req.repoPath, req.filePath, req.line)
    except Exception as e:
        logger.exception("context blame 분석 실패 — repo=%s file=%s line=%s", req.repoPath, req.filePath, req.line)
        raise HTTPException(status_code=500, detail=f"context blame 실패: {e}")

    response = BlameResponse(**result)
    try:
        dynamodb.put_blame_cache(req.repoPath, req.filePath, req.line, response.model_dump())
    except Exception:
        pass  # 캐시 저장 실패는 응답에 영향 없음

    return response
