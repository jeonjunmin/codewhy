"""Context Blame API 라우터.

POST /api/blame/context — 한 라인의 변경 사유를 분석해 반환한다.

흐름:
  1. blamed 커밋을 먼저 해석(git, 저렴) → 공유 백본에 repo/file/commit 행 확보
  2. (file_id, commit_id) 로 캐시 조회 — 적중 시 즉시 JSON 반환
     (같은 커밋이 바꾼 줄이면 줄 번호가 달라도 적중 — 커밋×파일 단위 dedup)
  3. 미스 시 분기(타임라인 /summary 와 동일한 듀얼 모드):
     - 노이즈 커밋(test/chore/docs) → Bedrock 없이 즉시 JSON
     - 의미있는 커밋 → SSE(text/event-stream) 스트림으로 설명 토큰을 실시간 전달
       (ai/blame_graph.stream_blame_graph 가 LangGraph StateGraph 를 구동, 스트림 종료 시점에 캐시 저장)

👤 담당: 개발자 A
"""

import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.blame_graph import run_blame_graph, stream_blame_graph
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


@router.post("/context")
async def context_blame(req: BlameRequest, db: AsyncSession = Depends(get_db)):
    # 0. blamed 커밋 해석 — 커밋 이력이 없으면(미커밋 파일/라인) 분석할 대상이 없으므로
    #    500 대신 안내 응답으로 단락한다. (analyze_blame 의 중복 git 호출도 함께 차단)
    try:
        info = git.get_blame_info(req.repoPath, req.filePath, req.line)
    except git.BlameUnavailable as e:
        logger.info("blame 불가 — %s (reason=%s)", e, e.reason)
        return BlameResponse(**service.uncommitted_response(e.reason))

    branch = git.get_current_branch(req.repoPath)
    ticket = extract_ticket(info.message, branch)

    # 1. 백본 행 확보 (캐시 키에 commit_id 포함)
    try:
        repo = await crud_common.get_or_create_repository(db, req.repoPath)
        file = await crud_common.get_or_create_file(db, repo.id, req.filePath)
        commit = await crud_common.upsert_commit(
            db,
            repo.id,
            info.commit_hash,
            author=info.author,
            committed_date=_parse_date(info.date),
            message=info.message,
            ticket=ticket,
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

    # 3. 미스 → 분기:
    #    - 노이즈 커밋(test/chore/docs): Bedrock·GitHub 호출이 없어 즉시 끝나므로 JSON 으로 응답.
    #    - 의미있는 커밋: SSE(text/event-stream) 스트림으로 설명 토큰을 실시간 전달하고,
    #      스트림 종료 시점에 stream_blame_graph(LangGraph)가 캐시에 저장한다(타임라인 /summary 와 동일 패턴).
    #    프런트는 응답 Content-Type 으로 두 경로를 구분한다(application/json vs text/event-stream).
    if service.is_noise_commit(info.message):
        try:
            # 노이즈 커밋도 동일한 blame_graph 를 ainvoke 로 통과시킨다(단일 파이프라인).
            # classify → noise_response → END 로 끝나 Bedrock·GitHub 호출이 없다.
            result = await run_blame_graph(
                req.repoPath, req.filePath, req.line,
                info=info, branch=branch, ticket=ticket,
            )
        except Exception as e:
            logger.exception("context blame 분석 실패 — repo=%s file=%s line=%s", req.repoPath, req.filePath, req.line)
            raise HTTPException(status_code=500, detail=f"context blame 실패: {e}")
        if commit is not None and file is not None and not result.get("aiDegraded"):
            try:
                await crud.save_blame(db, file.id, commit.id, result)
            except Exception:
                logger.warning("blame 캐시 저장 실패 (응답에는 영향 없음)", exc_info=True)
        return BlameResponse(**result)

    return StreamingResponse(
        stream_blame_graph(
            db, req.repoPath, req.filePath, req.line,
            info=info, branch=branch, ticket=ticket, commit=commit, file=file,
        ),
        media_type="text/event-stream",
        headers={
            # 중간 프록시/미들웨어가 응답을 버퍼링하지 못하도록 명시 (nginx 등)
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/ask", response_model=AskResponse)
def ask_blame(req: AskRequest):
    """AI에게 더 묻기 — 현재 라인 블레임 맥락 위에서 후속 질문에 답한다."""
    try:
        answer = service.ask_followup(req.repoPath, req.filePath, req.line, req.question)
    except git.BlameUnavailable as e:
        logger.info("blame ask 불가 — %s (reason=%s)", e, e.reason)
        return AskResponse(answer=service.uncommitted_response(e.reason)["explanation"])
    except Exception as e:
        logger.exception("blame ask 실패 — repo=%s file=%s line=%s", req.repoPath, req.filePath, req.line)
        raise HTTPException(status_code=500, detail=f"blame ask 실패: {e}")
    return AskResponse(answer=answer)
