"""Timeline Summary API 라우터.

POST /api/timeline/summary
  ① 확장 → 커밋 목록 포함 요청
  ② 백본 upsert + 전체 이력 조회 + 캐시 조회 (service.prepare_summary)
  ③ 분기:
     - 캐시 적중 → JSON(TimelineResponse) 즉시 응답
     - 캐시 미스 → StreamingResponse(SSE, text/event-stream) 로 Bedrock 토큰을
       실시간 전달하고, 스트림 종료 시점에 service.stream_summary 가 캐시에 저장

👤 담당: 개발자 B
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
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


@router.post("/summary")
async def timeline_summary(req: TimelineRequest, db: AsyncSession = Depends(get_db)):
    """캐시 적중 시 JSON, 미스 시 SSE 스트림(`text/event-stream`)을 반환한다.

    프런트는 응답의 Content-Type 으로 두 경로를 구분한다:
      - application/json            → TimelineResponse 그대로 사용
      - text/event-stream           → `data: {"delta": "..."}` 프레임을 누적 표시,
                                        마지막 `data: {"done": true, "summary":..., "milestones":...}`
                                        프레임으로 최종 결과 확정
    """
    file_path = _to_relpath(req.filePath, req.repoPath)
    commits_data = [c.model_dump() for c in req.commits]

    try:
        prepared = await service.prepare_summary(db, req.repoPath, file_path, commits_data)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("timeline summary 준비 실패 — repo=%s file=%s", req.repoPath, req.filePath)
        raise HTTPException(status_code=500, detail=f"timeline summary 실패: {e}")

    if prepared["cached"]:
        return TimelineResponse(**prepared["result"])

    return StreamingResponse(
        service.stream_summary(db, prepared["ctx"]),
        media_type="text/event-stream",
        headers={
            # 중간 프록시/미들웨어가 응답을 버퍼링하지 못하도록 명시 (nginx 등)
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
