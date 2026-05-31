"""Timeline Summary API 라우터.

POST /api/timeline/summary
  ① 확장 → 커밋 목록 포함 요청
  ② RDS upsert + 전체 이력 조회 (service.py)
  ③ Bedrock LangGraph 요약 (graph.py)
  결과를 DynamoDB에 캐시한 뒤 반환

👤 담당: 개발자 B
"""

import logging

from fastapi import APIRouter, HTTPException

from app.db import dynamodb
from app.features.timeline import service
from app.features.timeline.schemas import TimelineRequest, TimelineResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/summary", response_model=TimelineResponse)
async def timeline_summary(req: TimelineRequest):
    # DynamoDB 캐시 우선 조회 (실패해도 계속 진행)
    try:
        cached = dynamodb.get_timeline_cache(req.repoPath, req.filePath)
        if cached:
            return TimelineResponse(**cached)
    except Exception:
        pass

    # ②③ RDS 저장 → Bedrock 요약
    commits_data = [c.model_dump() for c in req.commits]
    try:
        result = await service.summarize(req.repoPath, req.filePath, commits_data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("timeline summary 실패 — repo=%s file=%s", req.repoPath, req.filePath)
        raise HTTPException(status_code=500, detail=f"timeline summary 실패: {e}")

    response = TimelineResponse(**result)
    try:
        dynamodb.put_timeline_cache(req.repoPath, req.filePath, response.model_dump())
    except Exception:
        pass

    return response
