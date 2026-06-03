"""브라운필드 온보딩 API 라우터.

POST /api/onboarding/backfill — 레포 전체 히스토리를 훑어 커밋↔문서 역링크를 사전 생성한다.

이미 SI 가 끝나고 SM 중인 레거시 프로젝트를 CodeWhy 에 올릴 때 1회 실행한다.
문서 대량 적재(POST /api/documents/bulk)가 선행돼 있어야 매칭이 이뤄진다.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import doc_index
from app.db.postgres import get_db
from app.features.onboarding import backfill
from app.features.onboarding.schemas import BackfillRequest, BackfillResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/backfill", response_model=BackfillResponse)
async def backfill_commits(req: BackfillRequest, db: AsyncSession = Depends(get_db)):
    try:
        result = await backfill.run_backfill(
            db,
            req.repoPath,
            since=req.since or "",
            limit=req.limit or 0,
            min_confidence=req.confidenceThreshold,
        )
    except Exception as e:
        logger.exception("백필 실패 — repo=%s", req.repoPath)
        raise HTTPException(status_code=500, detail=f"백필 실패: {e}")

    return BackfillResponse(indexConfigured=doc_index.is_enabled(), **result)
