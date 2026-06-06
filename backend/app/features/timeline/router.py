"""Timeline Summary API 라우터.

POST /api/timeline/summary
  ① 확장 → 커밋 목록 포함 요청
  ② 백본 upsert + 전체 이력 조회 (service.py)
  ③ commit_set_hash 캐시 조회 → 미스 시 Bedrock LangGraph 요약 후 캐시 저장 (service.py)

👤 담당: 개발자 B
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import get_db
from app.features.timeline import service
from app.features.timeline.schemas import TimelineRequest, TimelineResponse
from app.features.timeline.tasks import analyze_all_project_files

router = APIRouter()
logger = logging.getLogger(__name__)


# ── 프로젝트 전체 파일 분석 ───────────────────────────────────────────────────

class ProjectAnalyzeRequest(BaseModel):
    repo_path: str   # git 루트 절대 경로 (예: "C:/dev/codewhy")


class ProjectAnalyzeResponse(BaseModel):
    status: str
    message: str


@router.post("/files/analyze", response_model=ProjectAnalyzeResponse)
async def analyze_project_files(
    req: ProjectAnalyzeRequest,
    background_tasks: BackgroundTasks,
):
    """프로젝트의 모든 소스 파일을 백그라운드에서 순회 분석한다.

    - 사용자 대기 없이 즉시 STARTED 반환
    - BackgroundTasks 가 각 파일별 Skip / INSERT / UPDATE 를 수행
    - 터미널 로그에서 진행 상황 확인 가능
    """
    background_tasks.add_task(analyze_all_project_files, req.repo_path)
    logger.info("analyze_project_files: 배경 분석 시작 — %s", req.repo_path)
    return ProjectAnalyzeResponse(
        status="STARTED",
        message=f"{req.repo_path} 의 파일 분석을 시작했습니다.",
    )


@router.post("/summary", response_model=TimelineResponse)
async def timeline_summary(req: TimelineRequest, db: AsyncSession = Depends(get_db)):
    commits_data = [c.model_dump() for c in req.commits]
    try:
        result = await service.summarize(db, req.repoPath, req.filePath, commits_data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("timeline summary 실패 — repo=%s file=%s", req.repoPath, req.filePath)
        raise HTTPException(status_code=500, detail=f"timeline summary 실패: {e}")

    return TimelineResponse(**result)
