"""Requirement Trace API 라우터.

POST /api/trace/requirement — 파일과 연관된 GitHub Issue 기획 문서를 찾아 반환한다.

파일 단위 추적은 커밋↔이슈 연결을 commit_issues 에 캐시하고(불변), 이슈 메타만 매 요청
일괄 refresh 한다. 따라서 DB 세션(get_db)을 주입받아 캐시를 읽고 쓴다. 구버전 확장이
commits 없이 blame 단건만 보내면 캐시 없이 GitHub 실시간 조회로 폴백한다.

👤 담당: 개발자 C
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import vcs
from app.db import crud_common
from app.db.postgres import get_db
from app.features.traceability import service
from app.features.traceability.schemas import (
    DocumentMatch,
    TraceRequest,
    TraceResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/requirement", response_model=TraceResponse)
async def requirement_trace(req: TraceRequest, db: AsyncSession = Depends(get_db)):
    # 파일 커밋 이력도 blamed 커밋도 없으면 연관 문서를 찾을 대상이 없다.
    if not req.commits and req.blame is None:
        return TraceResponse(documents=[])

    remote = vcs.parse_remote(req.remoteUrl)
    try:
        if req.commits:
            # 파일 단위 — 백본(repo/file) 행을 확보하고 commit_issues 캐시로 역추적한다.
            repo = await crud_common.get_or_create_repository(db, req.repoPath)
            file = await crud_common.get_or_create_file(db, repo.id, req.filePath)
            matches = await service.trace_file(
                db,
                repo_id=repo.id,
                file_id=file.id,
                commits=req.commits,
                branch=req.branch,
                remote=remote,
            )
        else:
            # 폴백(구버전 확장) — blamed 커밋 단건 기준, 캐시 없음.
            matches = await asyncio.to_thread(
                service.trace, req.blame.commitHash, req.blame.message,
                branch=req.branch, remote=remote,
            )
    except Exception as e:
        logger.exception(
            "requirement trace 실패 — repo=%s file=%s commits=%d",
            req.repoPath, req.filePath, len(req.commits),
        )
        raise HTTPException(status_code=500, detail=f"requirement trace 실패: {e}")
    return TraceResponse(documents=[DocumentMatch(**m) for m in matches])
