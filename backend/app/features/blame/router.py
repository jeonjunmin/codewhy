"""Context Blame API 라우터.

POST /api/blame/context — 한 라인의 변경 사유를 분석해 반환한다.
DynamoDB 캐시가 있으면 그대로 돌려준다.

👤 담당: 개발자 A
"""

from fastapi import APIRouter, HTTPException

from app.db import dynamodb
from app.features.blame import service
from app.features.blame.schemas import BlameRequest, BlameResponse

router = APIRouter()


@router.post("/context", response_model=BlameResponse)
def context_blame(req: BlameRequest):
    cached = dynamodb.get_blame_cache(req.repoPath, req.filePath, req.line)
    if cached:
        return BlameResponse(**cached)

    try:
        result = service.analyze_blame(req.repoPath, req.filePath, req.line)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"context blame 실패: {e}")

    response = BlameResponse(**result)
    dynamodb.put_blame_cache(req.repoPath, req.filePath, req.line, response.model_dump())
    return response
