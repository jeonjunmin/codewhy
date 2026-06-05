"""프로젝트 초기화 및 타임라인 조회 API.

엔드포인트 목록:
  POST /api/v1/project/initialize  — 백그라운드 파일별 분석 시작
  GET  /api/v1/project/timeline    — DB에 저장된 타임라인 즉시 반환 (Bedrock 호출 없음)
  GET  /api/v1/project/status      — 분석된 파일 수 확인
"""

import logging
import os

from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.db.models import File, Repository, TimelineSummary
from app.db.postgres import AsyncSessionLocal
from app.features.project.tasks import analyze_files_timeline_task

router = APIRouter()
logger = logging.getLogger(__name__)


# ── 스키마 ────────────────────────────────────────────────────────────────────

class InitializeRequest(BaseModel):
    project_path: str


class InitializeResponse(BaseModel):
    status: str
    message: str = ""


class TimelineItem(BaseModel):
    file_path:   str
    summary:     str
    commit_hash: str
    type:        str
    domain:      str
    created_at:  datetime


# ── POST /initialize ──────────────────────────────────────────────────────────

@router.post("/initialize", response_model=InitializeResponse)
async def initialize_project(
    req: InitializeRequest,
    background_tasks: BackgroundTasks,
) -> InitializeResponse:
    """프로젝트 오픈 / 커밋 감지 시 호출 — 즉시 STARTED 반환, 분석은 백그라운드 처리."""
    logger.info("initialize: %s", req.project_path)
    background_tasks.add_task(analyze_files_timeline_task, req.project_path)
    return InitializeResponse(
        status="STARTED",
        message=f"'{req.project_path}' 파일별 분석 시작. 터미널 로그에서 진행 상황 확인.",
    )


# ── GET /timeline ─────────────────────────────────────────────────────────────

@router.get("/timeline", response_model=list[TimelineItem])
async def get_project_timeline(project_path: str) -> list[dict[str, Any]]:
    """DB에 저장된 타임라인 요약을 즉시 반환한다. Bedrock 을 절대 호출하지 않는다.

    3단계 역추적 쿼리:
      Step 1. repositories → identifier 로 repo_id 조회
      Step 2. files        → repo_id 로 파일 목록 조회
      Step 3. timeline_summaries → file_id IN (...) 로 요약 조회
    """
    folder_name = os.path.basename(os.path.normpath(project_path))

    async with AsyncSessionLocal() as db:

        # ── Step 1. repositories → repo_id ───────────────────────────────────
        repo: Repository | None = await db.scalar(
            select(Repository).where(Repository.identifier == folder_name)
        )
        if repo is None:
            raise HTTPException(
                status_code=404,
                detail=f"'{folder_name}' 레포지토리를 찾을 수 없습니다. "
                       "POST /initialize 를 먼저 호출해 분석을 완료하세요.",
            )

        # ── Step 2. files → file_id 목록 + path 매핑 ────────────────────────
        file_rows = (await db.scalars(
            select(File).where(File.repo_id == repo.id)
        )).all()

        if not file_rows:
            return []

        file_id_to_path: dict[int, str] = {f.id: f.file_path for f in file_rows}
        file_ids = list(file_id_to_path.keys())

        # ── Step 3. timeline_summaries → 요약 데이터 ─────────────────────────
        summary_rows = (await db.scalars(
            select(TimelineSummary).where(TimelineSummary.file_id.in_(file_ids))
        )).all()

    # ── 응답 조립 ─────────────────────────────────────────────────────────────
    result: list[dict[str, Any]] = []
    for s in summary_rows:
        milestones = s.milestones or {}
        result.append({
            "file_path":   file_id_to_path.get(s.file_id, ""),
            "summary":     s.summary or "",
            "commit_hash": s.commit_set_hash,
            "type":        milestones.get("type", ""),
            "domain":      milestones.get("domain", ""),
            "created_at":  s.created_at,
        })

    # file_path 기준 정렬
    result.sort(key=lambda x: x["file_path"])

    logger.info("timeline: %s → %d개 파일 반환", folder_name, len(result))
    return result


# ── GET /status ───────────────────────────────────────────────────────────────

@router.get("/status")
async def get_analyze_status(project_path: str) -> dict:
    """분석된 파일 수를 반환한다."""
    from sqlalchemy import func

    folder_name = os.path.basename(os.path.normpath(project_path))

    async with AsyncSessionLocal() as db:
        repo: Repository | None = await db.scalar(
            select(Repository).where(Repository.identifier == folder_name)
        )
        if repo is None:
            return {"project_path": project_path, "analyzed_files": 0, "status": "NOT_INITIALIZED"}

        file_ids = (await db.scalars(
            select(File.id).where(File.repo_id == repo.id)
        )).all()

        count = await db.scalar(
            select(func.count()).select_from(TimelineSummary)
            .where(TimelineSummary.file_id.in_(list(file_ids)))
        ) or 0

    return {
        "project_path":   project_path,
        "repo_id":        repo.id,
        "analyzed_files": count,
        "status":         "COMPLETED" if count > 0 else "PENDING",
    }
