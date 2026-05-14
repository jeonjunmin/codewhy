"""Timeline Summary API 라우터.

POST /api/timeline/summary — 파일 변경 이력의 요약과 마일스톤을 반환한다.
DynamoDB 캐시가 있으면 그대로 돌려준다.

👤 담당: 개발자 B
"""

from fastapi import APIRouter, HTTPException

from app.db import dynamodb
from app.features.timeline import service
from app.features.timeline.schemas import TimelineRequest, TimelineResponse

router = APIRouter()


@router.post("/summary", response_model=TimelineResponse)
def timeline_summary(req: TimelineRequest):
    cached = dynamodb.get_timeline_cache(req.repoPath, req.filePath)
    if cached:
        return TimelineResponse(**cached)

    try:
        result = service.summarize(req.repoPath, req.filePath)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"timeline summary 실패: {e}")

    response = TimelineResponse(**result)
    dynamodb.put_timeline_cache(req.repoPath, req.filePath, response.model_dump())
    return response
