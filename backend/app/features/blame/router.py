"""Context Blame API 라우터.

POST /api/blame/context — 한 라인의 변경 사유를 분석해 반환한다.

흐름:
  1. blamed 커밋을 먼저 해석(git, 저렴) → 공유 백본에 repo/file/commit 행 확보
  2. (file_id, commit_id) 로 캐시 조회 — 적중 시 즉시 JSON 반환
     (같은 커밋이 바꾼 줄이면 줄 번호가 달라도 적중 — 커밋×파일 단위 dedup)
  3. 미스 시 분기(타임라인 /summary 와 동일한 듀얼 모드):
     - 노이즈 커밋(test/chore/docs) → Bedrock 없이 즉시 JSON
     - 의미있는 커밋 → SSE(text/event-stream) 스트림으로 설명 토큰을 실시간 전달
       (스트림 종료 시점에 service.stream_blame 이 캐시에 저장)

👤 담당: 개발자 A
"""

import asyncio
import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import git, vcs
from app.core.tickets import extract_ticket
from app.db import crud_common
from app.db.postgres import AsyncSessionLocal, get_db
from app.features.blame import crud, service
from app.features.blame.schemas import (
    AskRequest,
    AskResponse,
    BlameRequest,
    BlameResponse,
    CacheClearRequest,
    GitCommitMeta,
    ReasonRequest,
    ReasonResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _to_blame_info(meta: GitCommitMeta) -> git.BlameInfo:
    """확장이 보낸 GitCommitMeta 를 내부 BlameInfo 로 변환한다(서버는 git 을 돌리지 않는다)."""
    return git.BlameInfo(
        commit_hash=meta.commitHash,
        author=meta.author,
        date=meta.date,
        message=meta.message,
        diff=meta.diff,
        added=meta.added,
        removed=meta.removed,
    )


@router.post("/context")
async def context_blame(req: BlameRequest):
    # 0. blamed 커밋 — 확장이 로컬 git 으로 해석해 보낸다. 커밋 이력이 없으면(미커밋 파일/라인)
    #    분석 대상이 없으므로 500 대신 안내 응답으로 단락한다.
    if req.blame is None:
        reason = req.unavailable or "no_history"
        logger.info("blame 불가 — reason=%s file=%s line=%s", reason, req.filePath, req.line)
        return BlameResponse(**service.uncommitted_response(reason))

    info = _to_blame_info(req.blame)
    branch = req.branch
    ticket = extract_ticket(info.message, branch)
    followups = [c.model_dump() for c in req.followups]
    remote = vcs.parse_remote(req.remoteUrl)
    line_history = [c.model_dump() for c in req.lineHistory]
    hash_key = service.line_scope_hash(line_history, info.message)

    # DB 작업(백본 확보·캐시 조회·노이즈 커밋 저장)은 스트림 시작 '전에' 끝내고 세션을 닫는다.
    #   StreamingResponse 동안 Depends(get_db) 세션이 열린 채 남으면, 클라이언트가 done 직후
    #   소켓을 닫을 때의 요청 취소가 그 세션 정리에 번져 커넥션이 terminate 되며 CancelledError
    #   noise 가 난다. 그래서 Depends 대신 사전 작업용 세션을 직접 열고 닫는다(스트림 분기는
    #   세션을 들고 가지 않고, 캐시 저장은 제너레이터가 자체 단명 세션으로 처리한다).
    commit_id: int | None = None
    file_id: int | None = None
    async with AsyncSessionLocal() as db:
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
            commit_id, file_id = commit.id, file.id
        except Exception:
            logger.warning("blame 백본 준비 실패 — 캐시 없이 분석만 진행", exc_info=True)
            commit = file = None

        # 2. 캐시 조회.
        #    - 단일 리비전 줄: 커밋×파일 스코프('' 키) — 같은 커밋의 다른 줄도 적중(설명 공유).
        #    - 멀티 리비전 줄: 라인 이력 해시 키 — '이력 반영' 설명을 줄 단위로 따로 캐시/적중한다
        #      (커밋 단위 캐시는 최신 변경 1건만 담아 이력을 반영하지 못하므로 분리).
        if commit is not None and file is not None:
            cached = await crud.get_cached_blame(db, file.id, commit, hash_key)
            if cached:
                # 라인 스코프 필드(이력 + 이슈 롤업)는 캐시 컬럼에 없으므로 확장이 보낸 라인 이력으로 다시 조립해 덧붙인다.
                cached.update(service.build_line_fields(line_history, info.commit_hash))
                # headline 은 캐시 컬럼에 없으니 설명에서 다시 뽑아 채운다.
                cached.setdefault("headline", service.extract_headline(cached.get("explanation", "")))
                return BlameResponse(**cached)

        # 3. 미스 → 노이즈 커밋(test/chore/docs): Bedrock·GitHub 호출이 없어 즉시 끝나므로 JSON 으로 응답.
        if service.is_noise_commit(info.message):
            try:
                result = await asyncio.to_thread(
                    service.analyze_blame,
                    req.repoPath, req.filePath, info,
                    branch=branch, ticket=ticket,
                    followups=followups, remote=remote, line_history=line_history,
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

    # 4. 의미있는 커밋 → 세션을 닫은 뒤 SSE 스트림. 설명 토큰을 실시간 전달하고, 제너레이터가
    #    스트림 종료 시점에 자체 단명 세션으로 캐시에 저장한다(타임라인 /summary 와 동일 패턴).
    #    프런트는 응답 Content-Type 으로 두 경로를 구분한다(application/json vs text/event-stream).
    return StreamingResponse(
        service.stream_blame(
            req.repoPath, req.filePath,
            info=info, branch=branch, ticket=ticket,
            commit_id=commit_id, file_id=file_id,
            followups=followups, remote=remote, line_history=line_history,
        ),
        media_type="text/event-stream",
        headers={
            # 중간 프록시/미들웨어가 응답을 버퍼링하지 못하도록 명시 (nginx 등)
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/reason", response_model=ReasonResponse)
async def commit_reason(req: ReasonRequest, db: AsyncSession = Depends(get_db)):
    """라인 수정 이력 항목 펼침 — 그 커밋의 변경 사유를 지연 생성한다.

    /context 와 같은 (file_id, commit_id) 캐시를 공유한다. 같은 커밋이면 어느 경로로
    먼저 분석됐든 적중하므로, 같은 라인을 다시 펼칠 때는 Bedrock 재호출 없이 즉시 답한다.
    """
    # 0. 임의 커밋 — 확장이 git 으로 해석해 보낸다. 잘린 이력/리베이스 등으로 해시가 사라졌으면
    #    commit=None 으로 와서 안내로 단락한다.
    if req.commit is None:
        logger.info("reason 불가 — hash=%s file=%s", req.hash, req.filePath)
        return ReasonResponse(reason=service.uncommitted_response("no_history")["explanation"])

    info = _to_blame_info(req.commit)
    branch = req.branch
    ticket = extract_ticket(info.message, branch)
    followups = [c.model_dump() for c in req.followups]
    remote = vcs.parse_remote(req.remoteUrl)

    # 1. 백본 행 확보 (캐시 키에 commit_id 포함) — /context 와 동일 패턴
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
        logger.warning("reason 백본 준비 실패 — 캐시 없이 분석만 진행", exc_info=True)
        commit = file = None

    # 2. 캐시 조회 — 적중 시 저장된 설명을 그대로 반환(Bedrock 미호출)
    if commit is not None and file is not None:
        cached = await crud.get_cached_blame(db, file.id, commit)
        if cached:
            return ReasonResponse(reason=cached.get("explanation", ""))

    # 3. 미스 → 분석(Bedrock). degraded 폴백이 아니면 캐시에 저장.
    try:
        result = await asyncio.to_thread(
            service.explain_commit_reason, req.repoPath, req.filePath, info,
            branch=branch, followups=followups, remote=remote,
        )
    except Exception as e:
        logger.exception("commit reason 분석 실패 — repo=%s file=%s hash=%s", req.repoPath, req.filePath, req.hash)
        raise HTTPException(status_code=500, detail=f"commit reason 실패: {e}")

    if commit is not None and file is not None and not result.get("aiDegraded"):
        try:
            await crud.save_blame(db, file.id, commit.id, result)
        except Exception:
            logger.warning("reason 캐시 저장 실패 (응답에는 영향 없음)", exc_info=True)

    return ReasonResponse(reason=result.get("explanation", ""), aiDegraded=bool(result.get("aiDegraded")))


@router.post("/ask", response_model=AskResponse)
def ask_blame(req: AskRequest):
    """AI에게 더 묻기 — 현재 라인 블레임 맥락 위에서 후속 질문에 답한다."""
    # blamed 커밋이 없으면(미커밋 라인) 분석 맥락이 없으므로 안내로 단락.
    if req.blame is None:
        reason = req.unavailable or "no_history"
        logger.info("blame ask 불가 — reason=%s file=%s line=%s", reason, req.filePath, req.line)
        return AskResponse(answer=service.uncommitted_response(reason)["explanation"])

    info = _to_blame_info(req.blame)
    remote = vcs.parse_remote(req.remoteUrl)
    try:
        answer = service.ask_followup(req.repoPath, req.filePath, info, req.question, remote=remote)
    except Exception as e:
        logger.exception("blame ask 실패 — repo=%s file=%s line=%s", req.repoPath, req.filePath, req.line)
        raise HTTPException(status_code=500, detail=f"blame ask 실패: {e}")
    return AskResponse(answer=answer)


@router.post("/cache/clear")
async def clear_blame_cache(req: CacheClearRequest, db: AsyncSession = Depends(get_db)):
    """현재 파일의 돋보기 설명 캐시를 모두 비운다 → {"deleted": 삭제건수}.

    파일 경로는 분석 저장 때(context_blame 의 get_or_create_file)와 동일하게 req.filePath 를
    그대로 키로 쓴다 — 블레임은 타임라인과 달리 _to_relpath 정규화를 하지 않으므로, 여기서도
    정규화하지 않아야 같은 File 행을 찾아 지운다.
    DELETE 대신 POST: 본문 있는 DELETE 를 버리는 프록시 대비(타임라인 /cache/clear 와 동일).
    """
    try:
        deleted = await crud.clear_explanations_for_file(db, req.repoPath, req.filePath)
    except Exception as e:
        logger.exception("blame 캐시 삭제 실패 — repo=%s file=%s", req.repoPath, req.filePath)
        raise HTTPException(status_code=500, detail=f"blame 캐시 삭제 실패: {e}")
    return {"deleted": deleted}
