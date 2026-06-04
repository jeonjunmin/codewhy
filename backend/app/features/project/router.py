"""프로젝트 초기화 API — 파일별 타임라인 분석 버전.

[구버전 제거]
  ✗ ProjectSummary 테이블 상태 관리 (PENDING/PROCESSING/COMPLETED)
  ✗ 프로젝트 전체 요약 → Bedrock 단일 호출

[신버전]
  ✓ POST /api/v1/project/initialize → analyze_files_timeline_task (BackgroundTasks)
  ✓ 파일별 순회 + 커밋 파싱 + Skip + timeline_summaries INSERT/UPDATE

스마트 Skip 은 analyze_files_timeline_task 내부에서 파일별로 수행된다:
  - type = test | chore           → Bedrock 미호출
  - commit_set_hash 일치          → Bedrock 미호출
  - 위 두 경우가 아닐 때만 LangGraph 실행
"""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.features.project.tasks import analyze_files_timeline_task

router = APIRouter()
logger = logging.getLogger(__name__)


# ── 스키마 ────────────────────────────────────────────────────────────────────

class InitializeRequest(BaseModel):
    project_path: str   # git 루트 절대 경로 (예: "C:/dev/codewhy")


class InitializeResponse(BaseModel):
    status: str
    message: str = ""


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.post("/initialize", response_model=InitializeResponse)
async def initialize_project(
    req: InitializeRequest,
    background_tasks: BackgroundTasks,
) -> InitializeResponse:
    """프로젝트가 처음 열릴 때, 또는 커밋·저장 이벤트가 감지될 때 호출한다.

    사용자 대기 없이 즉시 STARTED 를 반환하고,
    BackgroundTasks 가 파일별 분석을 조용히 처리한다.

    스마트 Skip 로직이 태스크 내부에서 파일별로 동작하므로
    이미 분석된 파일은 Bedrock 을 호출하지 않는다.
    """
    logger.info("initialize: 파일별 타임라인 분석 시작 — %s", req.project_path)

    background_tasks.add_task(analyze_files_timeline_task, req.project_path)

    return InitializeResponse(
        status="STARTED",
        message=(
            f"'{req.project_path}' 의 파일별 분석을 백그라운드에서 시작했습니다. "
            "터미널 로그에서 진행 상황을 확인하세요."
        ),
    )


@router.get("/status", response_model=dict)
async def get_analyze_status(project_path: str) -> dict:
    """timeline_summaries 테이블에서 분석된 파일 수를 반환한다."""
    from sqlalchemy import func, select
    from app.db.models import TimelineSummary
    from app.db.postgres import AsyncSessionLocal
    from app.features.timeline.file_summary import _file_id   # noqa

    async with AsyncSessionLocal() as db:
        count = await db.scalar(
            select(func.count()).select_from(TimelineSummary)
        )

    return {
        "project_path":   project_path,
        "analyzed_files": count or 0,
        "message":        "GET /api/timeline/files/analyze 로 상세 파일 목록 조회 가능",
    }
