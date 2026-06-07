"""Timeline Summary API 라우터.

POST /api/timeline/summary
  ① 확장 → 커밋 목록 포함 요청
  ② 백본 upsert + 전체 이력 조회 (service.py)
  ③ commit_set_hash 캐시 조회 → 미스 시 Bedrock LangGraph 요약 후 캐시 저장 (service.py)

👤 담당: 개발자 B
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import get_db
from app.features.timeline import service
from app.features.timeline.schemas import TimelineRequest, TimelineResponse

router = APIRouter()
logger = logging.getLogger(__name__)


def _to_relpath(file_path: str, repo_path: str) -> str:
    """절대 경로를 레포 루트 기준 상대 경로(포워드 슬래시)로 정규화한다."""
    try:
        rel = os.path.relpath(file_path, repo_path)
    except ValueError:
        rel = file_path
    return rel.replace('\\', '/')


@router.post("/summary", response_model=TimelineResponse)
async def timeline_summary(req: TimelineRequest, db: AsyncSession = Depends(get_db)):
    file_path = _to_relpath(req.filePath, req.repoPath)
    commits_data = [c.model_dump() for c in req.commits]
    try:
        result = await service.summarize(db, req.repoPath, file_path, commits_data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("timeline summary 실패 — repo=%s file=%s", req.repoPath, req.filePath)
        raise HTTPException(status_code=500, detail=f"timeline summary 실패: {e}")

    return TimelineResponse(**result)
