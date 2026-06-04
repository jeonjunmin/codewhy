"""Context Blame API 라우터.

POST /api/blame/context — 한 라인의 변경 사유를 분석해 반환한다.

흐름:
  1. blamed 커밋을 먼저 해석(git, 저렴) → 공유 백본에 repo/file/commit 행 확보
  2. (file_id, commit_id) 로 캐시 조회 — 적중 시 즉시 반환
     (같은 커밋이 바꾼 줄이면 줄 번호가 달라도 적중 — 커밋×파일 단위 dedup)
  3. 미스 시 service.analyze_blame(Bedrock) 실행 후 캐시에 저장

👤 담당: 개발자 A
"""

import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import git
from app.core.tickets import extract_ticket
from app.db import crud_common
from app.db.postgres import get_db
from app.features.blame import crud, service
from app.features.blame.schemas import AskRequest, AskResponse, BlameRequest, BlameResponse

router = APIRouter()
logger = logging.getLogger(__name__)


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


@router.post("/context", response_model=BlameResponse)
async def context_blame(req: BlameRequest, db: AsyncSession = Depends(get_db)):
    # 1. blamed 커밋 해석 + 백본 행 확보 (캐시 키에 commit_id 포함)
    info = None
    try:
        info = git.get_blame_info(req.repoPath, req.filePath, req.line)
        branch = git.get_current_branch(req.repoPath)
        repo = await crud_common.get_or_create_repository(db, req.repoPath)
        file = await crud_common.get_or_create_file(db, repo.id, req.filePath)
        commit = await crud_common.upsert_commit(
            db,
            repo.id,
            info.commit_hash,
            author=info.author,
            committed_date=_parse_date(info.date),
            message=info.message,
            ticket=extract_ticket(info.message, branch),
        )
        await crud_common.link_commit_file(db, commit.id, file.id, info.added, info.removed)
        await db.commit()
    except Exception:
        logger.warning("blame 백본 준비 실패 — 캐시 없이 분석만 진행", exc_info=True)
        commit = file = None

    # 2. 캐시 조회 (커밋×파일 단위 — 같은 커밋의 다른 줄도 적중)
    if commit is not None and file is not None:
        cached = await crud.get_cached_blame(db, file.id, commit)
        if cached:
            return BlameResponse(**cached)

    # 3. 미스 → 분석 후 캐시 저장
    #    1단계에서 이미 구한 info 를 넘겨 service 의 중복 git blame 을 피한다(없으면 service 가 재조회).
    try:
        result = service.analyze_blame(req.repoPath, req.filePath, req.line, info=info)
    except Exception as e:
        logger.exception("context blame 분석 실패 — repo=%s file=%s line=%s", req.repoPath, req.filePath, req.line)
        raise HTTPException(status_code=500, detail=f"context blame 실패: {e}")

    if commit is not None and file is not None:
        try:
            await crud.save_blame(db, file.id, commit.id, result)
        except Exception:
            logger.warning("blame 캐시 저장 실패 (응답에는 영향 없음)", exc_info=True)

    return BlameResponse(**result)


@router.post("/ask", response_model=AskResponse)
def ask_blame(req: AskRequest):
    """AI에게 더 묻기 — 현재 라인 블레임 맥락 위에서 후속 질문에 답한다."""
    try:
        answer = service.ask_followup(req.repoPath, req.filePath, req.line, req.question)
    except Exception as e:
        logger.exception("blame ask 실패 — repo=%s file=%s line=%s", req.repoPath, req.filePath, req.line)
        raise HTTPException(status_code=500, detail=f"blame ask 실패: {e}")
    return AskResponse(answer=answer)
