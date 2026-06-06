"""파일별 타임라인 요약 — 파싱 · Skip · LangGraph · INSERT/UPDATE.

FK 처리:
  timeline_summaries.file_id → files.id (FK 제약)
  → 분석 전 files 테이블에 upsert 해 실제 DB id 를 확보한 뒤 사용

전체 결정 트리 (파일 하나당):
  ① 커밋 없음                   → skip_no_commits
  ② type = test | chore          → skip_type       (Bedrock 미호출)
  ③ DB 존재 + 해시 일치          → skip_hash       (Bedrock 미호출)
  ④ DB 없음                      → LangGraph → INSERT
  ⑤ DB 존재 + 해시 불일치        → LangGraph → UPDATE
"""

import asyncio
import logging
import re
from datetime import datetime

from sqlalchemy import select

from app.ai.timeline_file_graph import run_file_timeline_graph
from app.core import git
from app.db import crud_common
from app.db.models import TimelineSummary
from app.db.postgres import AsyncSessionLocal

logger = logging.getLogger(__name__)

SKIP_TYPES: frozenset[str] = frozenset({"test", "chore"})
_COMMIT_RE = re.compile(r"^(?P<type>\w+)(?:\[(?P<domain>[^\]]+)\])?:")


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def parse_commit_message(message: str) -> dict[str, str]:
    """'feat[auth]: 메시지' → {"type": "feat", "domain": "auth"}"""
    m = _COMMIT_RE.match(message.strip())
    if m:
        return {
            "type":   m.group("type").lower(),
            "domain": (m.group("domain") or "").lower(),
        }
    return {"type": "unknown", "domain": ""}


def should_skip(commit_type: str) -> bool:
    return commit_type.lower() in SKIP_TYPES


def _format_commits(commits: list[dict]) -> str:
    return "\n".join(
        f"- [{c.get('date', '')}] {c.get('subject', '')} (by {c.get('author', '')})"
        for c in commits
    )


async def _upsert_file(repo_path: str, file_path: str) -> int:
    """repositories + files 테이블에서 file_id 를 가져오거나 INSERT 후 반환한다.

    File 모델은 repo_path 컬럼이 없고 repo_id(FK) 를 사용하므로,
    crud_common 을 통해 Repository 를 먼저 확보한 뒤 File 을 upsert 한다.
    """
    async with AsyncSessionLocal() as db:
        repo = await crud_common.get_or_create_repository(db, repo_path)
        file = await crud_common.get_or_create_file(db, repo.id, file_path)
        await db.commit()
        return file.id


# ── 메인 ──────────────────────────────────────────────────────────────────────

async def analyze_and_save_file_summary(
    repo_path: str,
    file_path: str,
    commit_set_hash: str,
) -> str:
    """파일 하나에 대해 전체 분석 파이프라인을 실행하고 결과 상태를 반환한다.

    Returns:
        "skip_no_commits" | "skip_type" | "skip_hash" | "inserted" | "updated" | "failed"
    """

    # ── Step 1. git 커밋 이력 수집 (동기 subprocess → 스레드 실행) ─────────────
    try:
        commits: list[dict] = await asyncio.to_thread(
            git.get_file_log, repo_path, file_path
        )
    except Exception as exc:
        logger.warning("[file_summary] git log 실패 — %s : %s", file_path, exc)
        return "skip_no_commits"

    if not commits:
        logger.info("[file_summary] 커밋 없음 → skip — %s", file_path)
        return "skip_no_commits"

    # ── Step 2. 최신 커밋 메시지 파싱 ─────────────────────────────────────────
    latest_subject = commits[0].get("subject", "")
    parsed        = parse_commit_message(latest_subject)
    commit_type   = parsed["type"]
    commit_domain = parsed["domain"]

    logger.info("[file_summary] 파싱 — %s  type=%s  domain=%s",
                file_path, commit_type, commit_domain)

    # ── Step 3. type 기반 Skip (test / chore) ─────────────────────────────────
    if should_skip(commit_type):
        logger.info("[file_summary] type='%s' → skip_type — %s", commit_type, file_path)
        return "skip_type"

    # ── Step 4. files 테이블 upsert → 실제 DB file_id 확보 (FK 원본) ─────────
    try:
        file_id_val: int = await _upsert_file(repo_path, file_path)
    except Exception as exc:
        logger.exception("[file_summary] files upsert 실패 — %s : %s", file_path, exc)
        return "failed"

    logger.info("[file_summary] file_id=%d 확보 — %s", file_id_val, file_path)

    # ── Step 5. timeline_summaries 조회 (file_id 기준) + 해시 비교 ───────────
    async with AsyncSessionLocal() as db:
        existing: TimelineSummary | None = await db.scalar(
            select(TimelineSummary).where(TimelineSummary.file_id == file_id_val)
        )
        existing_hash  = existing.commit_set_hash if existing else None
        existing_db_id = existing.id if existing else None

    if existing_hash == commit_set_hash:
        logger.info("[file_summary] 해시 일치 → skip_hash — %s", file_path)
        return "skip_hash"

    # ── Step 6. LangGraph ainvoke() → Bedrock 요약 ───────────────────────────
    commits_text = _format_commits(commits)
    logger.info("[file_summary] LangGraph 시작 — %s  커밋 %d건", file_path, len(commits))

    try:
        summary = await run_file_timeline_graph(
            file_path=file_path,
            commits_text=commits_text,
            commit_type=commit_type,
            commit_domain=commit_domain,
        )
        logger.info("[file_summary] Bedrock 완료 — %s  요약 %d자\n%s",
                    file_path, len(summary), summary[:120])
    except Exception as exc:
        logger.exception("[file_summary] LangGraph 실패 — %s : %s", file_path, exc)
        return "failed"

    # ── Step 7. milestones JSON 구성 ──────────────────────────────────────────
    milestones: dict = {
        "type":          commit_type,
        "domain":        commit_domain,
        "commit_count":  len(commits),
        "latest_commit": latest_subject,
    }

    # ── Step 8. asyncpg INSERT or UPDATE ──────────────────────────────────────
    async with AsyncSessionLocal() as db:

        if existing_db_id is None:
            # ── INSERT ────────────────────────────────────────────────────────
            logger.info("[file_summary] INSERT 직전 — file_id=%d  milestones=%s",
                        file_id_val, milestones)
            new_row = TimelineSummary(
                file_id         = file_id_val,
                commit_set_hash = commit_set_hash,
                summary         = summary,
                milestones      = milestones,
                created_at      = datetime.utcnow(),
            )
            db.add(new_row)
            await db.commit()
            await db.refresh(new_row)
            logger.info("[file_summary] INSERT 완료 — id=%d  file=%s",
                        new_row.id, file_path)
            return "inserted"

        else:
            # ── UPDATE ────────────────────────────────────────────────────────
            row: TimelineSummary | None = await db.scalar(
                select(TimelineSummary).where(TimelineSummary.id == existing_db_id)
            )
            if row is None:
                # 동시성 경합: 삭제된 경우 INSERT 로 대체
                db.add(TimelineSummary(
                    file_id=file_id_val, commit_set_hash=commit_set_hash,
                    summary=summary, milestones=milestones,
                    created_at=datetime.utcnow(),
                ))
                await db.commit()
                return "inserted"

            logger.info("[file_summary] UPDATE 직전 — id=%d  %s → %s",
                        row.id, existing_hash, commit_set_hash)
            row.commit_set_hash = commit_set_hash
            row.summary         = summary
            row.milestones      = milestones
            await db.commit()
            await db.refresh(row)
            logger.info("[file_summary] UPDATE 완료 — id=%d  file=%s", row.id, file_path)
            return "updated"
