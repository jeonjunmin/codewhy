from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import git_service, ai_service
from app.db import dynamodb

router = APIRouter()


class TimelineRequest(BaseModel):
    filePath: str
    repoPath: str


class Milestone(BaseModel):
    date: str
    description: str


class TimelineResponse(BaseModel):
    summary: str
    milestones: list[Milestone]


@router.post("/summary", response_model=TimelineResponse)
def timeline_summary(req: TimelineRequest):
    cached = dynamodb.get_timeline_cache(req.repoPath, req.filePath)
    if cached:
        return TimelineResponse(**cached)

    try:
        commits = git_service.get_file_log(req.repoPath, req.filePath)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"git log 실패: {e}")

    if not commits:
        raise HTTPException(status_code=404, detail="커밋 이력이 없습니다.")

    result_data = ai_service.summarize_timeline(commits)
    result = TimelineResponse(**result_data)

    dynamodb.put_timeline_cache(req.repoPath, req.filePath, result.model_dump())
    return result
