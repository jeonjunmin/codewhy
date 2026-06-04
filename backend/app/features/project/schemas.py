"""프로젝트 초기화 / 요약 조회 스키마."""

from datetime import datetime

from pydantic import BaseModel

from app.db.models import ProjectStatus


class InitializeRequest(BaseModel):
    project_path: str


class InitializeResponse(BaseModel):
    # "STARTED" | "NO_CHANGES" | "PROCESSING" | "ALREADY_COMPLETED"
    status: str
    message: str = ""


class ProjectSummaryResponse(BaseModel):
    project_path: str
    status: ProjectStatus
    summary_text: str | None
    updated_at: datetime
