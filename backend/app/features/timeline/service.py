"""Timeline Summary 비즈니스 로직.

데이터 흐름:
  ① 확장이 보낸 commits
  → ② PostgreSQL 백본에 upsert + 파일 전체 이력 조회 (crud.py)
  → ③ commit_set_hash 로 요약 캐시 조회
       — 적중 시 prepare_summary() 가 즉시 결과를 반환 (router 가 JSON 응답)
       — 미스 시 prepare_summary() 가 스트리밍 컨텍스트를 반환 (router 가 SSE 스트림 응답)
  → ④ 미스일 때만: git diff 추출 → stream_summary() 가 Bedrock 토큰을
       SSE(`data: ...`) 프레임으로 실시간 전달하고, 스트림 종료 시점에 누적 텍스트를
       파싱해 timeline_summaries 캐시에 저장한다

👤 담당: 개발자 B
"""

import asyncio
import hashlib
import json
import logging
import re
import subprocess
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.timeline_file_graph import parse_ai_response, stream_file_summary
from app.core.commit_classifier import filter_meaningful
from app.features.timeline import crud

logger = logging.getLogger(__name__)

_COMMIT_RE = re.compile(r"^(?P<type>\w+)(?:\[(?P<domain>[^\]]+)\])?:")


def compute_commit_set_hash(commits: list[dict]) -> str:
    """파일의 커밋 목록으로부터 타임라인 요약 캐시 키(SHA-256 hex)를 계산한다.

    노이즈 커밋(test/chore/docs)은 LangGraph 진입 전에 이미 걸러지므로
    캐시 키 계산에서도 동일하게 제외한다 — 노이즈 커밋만 추가됐을 때
    캐시가 불필요하게 무효화되는 것을 방지한다 (TIMELINE_OPTIMIZATION_PLAN.md §2 C).
    """
    target = filter_meaningful(commits)
    serialized = "\n".join(sorted(c["hash"] for c in target))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _get_file_diff(repo_path: str, file_path: str) -> str:
    """git show HEAD -- file_path 로 최신 커밋의 diff 를 반환한다."""
    try:
        return subprocess.check_output(
            ["git", "show", "HEAD", "--", file_path],
            cwd=repo_path, text=True, encoding="utf-8", timeout=10,
        )
    except Exception:
        return ""


def _parse_commit(message: str) -> dict[str, str]:
    m = _COMMIT_RE.match(message.strip())
    if m:
        return {"type": m.group("type").lower(), "domain": (m.group("domain") or "").lower()}
    return {"type": "unknown", "domain": ""}


def _format_commits(commits: list[dict]) -> str:
    return "\n".join(
        f"- [{c.get('date', '')}] {c.get('subject', '')} (by {c.get('author', '')})"
        for c in commits
    )


async def prepare_summary(
    db: AsyncSession, repo_path: str, file_path: str, commits: list[dict]
) -> dict:
    """① 백본 upsert + ② 이력 조회 + ③ 캐시 조회까지 수행하고, 라우터가 응답 형식을
    결정할 수 있도록 결과를 분기해 반환한다.

    캐시 적중: {"cached": True, "result": {"summary","milestones"}}  → 라우터가 JSON 즉시 응답
    캐시 미스: {"cached": False, "ctx": {...}}                       → 라우터가 SSE 스트림 응답
    """
    logger.info("[timeline] ▶ prepare_summary 시작 — repo=%s  file=%s  ext_commits=%d건",
                repo_path, file_path, len(commits))

    # ② 백본 upsert + 파일 전체 이력 조회
    file = await crud.upsert_commits(db, repo_path, file_path, commits)
    logger.info("[timeline] file 확보 — file_id=%d  repo_id=%d", file.id, file.repo_id)

    stored = await crud.get_commits(db, file.id)
    logger.info("[timeline] DB 커밋 이력 — %d건", len(stored))
    if not stored:
        raise ValueError("저장된 커밋 이력이 없습니다.")

    # ③ 요약 캐시 조회
    set_hash = compute_commit_set_hash(stored)
    logger.info("[timeline] commit_set_hash=%s", set_hash[:16] + "…")

    cached = await crud.get_cached_summary(db, file.id, set_hash)
    if cached:
        logger.info("[timeline] ✅ 캐시 적중 — file_id=%d", file.id)
        return {"cached": True, "result": {"summary": cached.summary, "milestones": cached.milestones or []}}

    logger.info("[timeline] ❌ 캐시 미스 — 스트리밍 응답으로 전환")
    return {
        "cached": False,
        "ctx": {
            "file": file,
            "set_hash": set_hash,
            "repo_path": repo_path,
            "file_path": file_path,
            "stored": stored,
        },
    }


async def stream_summary(db: AsyncSession, ctx: dict) -> AsyncGenerator[str, None]:
    """캐시 미스 시 Bedrock 토큰을 SSE(`data: ...\\n\\n`) 프레임으로 실시간 전달한다.

    스트림이 끝나면(요구사항 3) 누적 텍스트를 파싱해 timeline_summaries 캐시에
    저장하는 DB 적재 로직을 그대로 수행한다 — 캐시 히트 로직과의 정합성 유지.
    """
    file       = ctx["file"]
    set_hash   = ctx["set_hash"]
    repo_path  = ctx["repo_path"]
    file_path  = ctx["file_path"]
    stored     = ctx["stored"]

    # 미스 → git diff 추출 → Bedrock 입력 구성
    diff_text = await asyncio.to_thread(_get_file_diff, repo_path, file_path)
    commits_text = diff_text.strip() if diff_text.strip() else _format_commits(stored)
    logger.info("[timeline] bedrock 입력 — diff=%d자  (diff_empty=%s)",
                len(commits_text), not diff_text.strip())

    latest = stored[0] if stored else {}
    parsed = _parse_commit(latest.get("subject", ""))
    logger.info("[timeline] 🔴 스트리밍 시작 — file=%s  type=%s  domain=%s",
                file_path, parsed["type"], parsed["domain"])

    full_text = ""
    try:
        async for delta in stream_file_summary(
            file_path=file_path,
            commits_text=commits_text,
            commit_type=parsed["type"],
            commit_domain=parsed["domain"],
        ):
            full_text += delta
            yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
    except Exception as exc:
        logger.exception("[timeline] 스트리밍 중 오류 — file=%s : %s", file_path, exc)
        yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
        return

    # 스트림 종료 → 누적 텍스트 파싱 + 캐시 저장 (요구사항 3)
    result = parse_ai_response(full_text)
    logger.info("[timeline] Bedrock 스트리밍 완료 — summary=%d자  milestones=%d건",
                len(result.get("summary", "")), len(result.get("milestones", [])))

    await crud.save_summary(db, file.id, set_hash, result)
    logger.info("[timeline] 캐시 저장 완료 — file_id=%d  hash=%s", file.id, set_hash[:16] + "…")

    yield f"data: {json.dumps({'done': True, **result}, ensure_ascii=False)}\n\n"
