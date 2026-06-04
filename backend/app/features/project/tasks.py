"""프로젝트 파일별 타임라인 분석 태스크 — 프로덕션 최종본.

DB 흐름 (파일 하나당 단일 세션 커밋):

  [읽기 세션]
    timeline_summary_cache 조회 → commit_hash 비교 → skip 여부 결정

  [LangGraph / Bedrock]  ← DB 세션 없이 실행 (커넥션 점유 방지)

  [쓰기 세션 — 단일 트랜잭션]
    ① files        SELECT or INSERT flush → file_id 확보
    ② timeline_summary_cache  INSERT / UPDATE
    ③ timeline_summaries      INSERT / UPDATE
    ④ await db.commit()  (세 테이블 동시 반영)

태스크 시작 시 1회:
  repositories  SELECT or INSERT → repo_id 확보
  이후 모든 파일에서 해당 repo_id 재사용
"""

import asyncio
import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.ai.timeline_file_graph import run_file_timeline_graph
from app.core import git
from app.db.models import File, Repository, TimelineSummary, TimelineSummaryCache
from app.db.postgres import AsyncSessionLocal

logger = logging.getLogger(__name__)

SKIP_TYPES: frozenset[str] = frozenset({"test", "chore"})

_SOURCE_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx",
    ".java", ".kt", ".go", ".rs", ".swift",
    ".c", ".cpp", ".cs",
})

_COMMIT_RE = re.compile(r"^(?P<type>\w+)(?:\[(?P<domain>[^\]]+)\])?:")


# ── git 헬퍼 ──────────────────────────────────────────────────────────────────

def _get_tracked_files(repo_path: str) -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "ls-files"],
            cwd=repo_path, text=True, encoding="utf-8", timeout=15,
        )
        return [
            f.strip() for f in out.splitlines()
            if Path(f.strip()).suffix in _SOURCE_EXTENSIONS
        ]
    except Exception as exc:
        logger.warning("[task] git ls-files 실패 — %s : %s", repo_path, exc)
        return []


def _get_repo_head(repo_path: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path, text=True, encoding="utf-8", timeout=5,
        ).strip()
    except Exception:
        return "unknown"


def _parse_commit(message: str) -> dict[str, str]:
    m = _COMMIT_RE.match(message.strip())
    if m:
        return {
            "type":   m.group("type").lower(),
            "domain": (m.group("domain") or "").lower(),
        }
    return {"type": "unknown", "domain": ""}


def _format_commits(commits: list[dict]) -> str:
    return "\n".join(
        f"- [{c.get('date', '')}] {c.get('subject', '')} (by {c.get('author', '')})"
        for c in commits
    )


# ── repositories: 진짜 repo_id 동적 확보 ─────────────────────────────────────

async def _get_or_create_repo_id(project_path: str) -> int:
    """repositories 테이블에서 identifier 로 repo_id 를 가져오거나 INSERT 한다.

    identifier / name = os.path.basename(project_path) (폴더명)
    파일 루프 시작 전 1회 호출 — 이후 전체 파일에서 동일 repo_id 재사용.
    """
    folder_name = os.path.basename(os.path.normpath(project_path))  # 예: "codewhy_test"

    async with AsyncSessionLocal() as db:
        repo: Repository | None = await db.scalar(
            select(Repository).where(Repository.identifier == folder_name)
        )

        if repo is not None:
            logger.info("[task] repositories 기존 행 — repo_id=%d  identifier=%s",
                        repo.id, folder_name)
            return repo.id

        # 없으면 INSERT (identifier + name = 폴더명)
        new_repo = Repository(
            identifier = folder_name,
            name       = folder_name,
            created_at = datetime.utcnow(),
        )
        db.add(new_repo)
        await db.flush()            # DB 시퀀스로 id 발급
        await db.refresh(new_repo)  # 자동 생성 id 동기화
        await db.commit()

        logger.info("[task] repositories INSERT — repo_id=%d  identifier=%s",
                    new_repo.id, folder_name)
        return new_repo.id


# ── 파일 단위 처리 ─────────────────────────────────────────────────────────────

async def _process_file(repo_path: str, file_path: str, repo_id: int) -> str:
    """파일 하나의 전체 분석 파이프라인을 실행하고 결과 상태를 반환한다."""

    # ── Step 1. git 커밋 이력 수집 ────────────────────────────────────────────
    try:
        commits: list[dict] = await asyncio.to_thread(
            git.get_file_log, repo_path, file_path
        )
    except Exception as exc:
        logger.warning("[task] git log 실패 — %s : %s", file_path, exc)
        return "skip_no_commits"

    if not commits:
        return "skip_no_commits"

    latest_hash    = commits[0]["hash"]
    latest_subject = commits[0].get("subject", "")

    # ── Step 2. [읽기 세션] timeline_summary_cache → commit_hash 비교 ─────────
    async with AsyncSessionLocal() as db:
        cache_row: TimelineSummaryCache | None = await db.scalar(
            select(TimelineSummaryCache).where(
                TimelineSummaryCache.repo_path == repo_path,
                TimelineSummaryCache.file_path == file_path,
            )
        )
    # 세션 닫힘 — 이후 LangGraph 동안 커넥션 점유 없음

    if cache_row is not None:
        cached_hash = (cache_row.data or {}).get("commit_hash", "")
        if cached_hash == latest_hash:
            logger.info("[task]   ⏭  해시 일치 → skip_hash — %s", file_path)
            return "skip_hash"

    # ── Step 3. 커밋 메시지 파싱 + type 기반 Skip ────────────────────────────
    parsed        = _parse_commit(latest_subject)
    commit_type   = parsed["type"]
    commit_domain = parsed["domain"]

    logger.info("[task]   파싱 — %s  type=%s  domain=%s",
                file_path, commit_type, commit_domain)

    if commit_type in SKIP_TYPES:
        logger.info("[task]   ⏭  type='%s' → skip_type — %s", commit_type, file_path)
        return "skip_type"

    # ── Step 4. LangGraph ainvoke() → Bedrock 요약 (DB 세션 없이 실행) ────────
    logger.info("[task]   🤖 LangGraph 시작 — %s  커밋 %d건", file_path, len(commits))

    try:
        summary: str = await run_file_timeline_graph(
            file_path=file_path,
            commits_text=_format_commits(commits),
            commit_type=commit_type,
            commit_domain=commit_domain,
        )
        logger.info("[task]   ✅ Bedrock 완료 — %s  요약 %d자", file_path, len(summary))
    except Exception as exc:
        logger.exception("[task]   ❌ LangGraph 실패 — %s : %s", file_path, exc)
        return "failed"

    milestones: dict = {"type": commit_type, "domain": commit_domain}
    cache_data: dict = {
        "commit_hash": latest_hash,
        "type":        commit_type,
        "domain":      commit_domain,
    }

    # ── Step 5. [쓰기 세션] files + cache + summaries 단일 트랜잭션 ──────────
    try:
        async with AsyncSessionLocal() as db:

            # ── 5a. files SELECT or INSERT → file_id 확보 ────────────────────
            file_row: File | None = await db.scalar(
                select(File).where(
                    File.repo_id   == repo_id,
                    File.file_path == file_path,
                )
            )

            if file_row is None:
                new_file = File(repo_id=repo_id, file_path=file_path)
                db.add(new_file)
                await db.flush()            # DB 시퀀스로 id 발급
                await db.refresh(new_file)  # 자동 생성 id 동기화
                file_id: int = new_file.id
                logger.info("[task]   files INSERT — file_id=%d  path=%s",
                            file_id, file_path)
            else:
                file_id = file_row.id
                logger.info("[task]   files 기존 행 — file_id=%d", file_id)

            # ── 5b. timeline_summary_cache INSERT / UPDATE ────────────────────
            if cache_row is None:
                db.add(TimelineSummaryCache(
                    repo_path  = repo_path,
                    file_path  = file_path,
                    data       = cache_data,
                    created_at = datetime.utcnow(),
                ))
            else:
                cache_fresh: TimelineSummaryCache | None = await db.scalar(
                    select(TimelineSummaryCache).where(
                        TimelineSummaryCache.repo_path == repo_path,
                        TimelineSummaryCache.file_path == file_path,
                    )
                )
                if cache_fresh:
                    cache_fresh.data = cache_data

            # ── 5c. timeline_summaries INSERT / UPDATE ────────────────────────
            existing: TimelineSummary | None = await db.scalar(
                select(TimelineSummary).where(TimelineSummary.file_id == file_id)
            )

            if existing is None:
                db.add(TimelineSummary(
                    file_id         = file_id,
                    commit_set_hash = latest_hash,
                    summary         = summary,
                    milestones      = milestones,
                    created_at      = datetime.utcnow(),
                ))
                op = "inserted"
            else:
                existing.commit_set_hash = latest_hash
                existing.summary         = summary
                existing.milestones      = milestones
                op = "updated"

            logger.info(
                "[task]   💾 commit 직전 — file_id=%d  op=%s  hash=%s",
                file_id, op, latest_hash[:8],
            )

            # ── 5d. 세 테이블 동시 반영 ──────────────────────────────────────
            await db.commit()

            if existing is not None:
                await db.refresh(existing)

            logger.info("[task]   💾 commit 완료 — file_id=%d  op=%s", file_id, op)
            return op

    except Exception as exc:
        logger.exception("[task]   ❌ DB 저장 실패 — %s : %s", file_path, exc)
        return "failed"


# ── 메인 태스크 ───────────────────────────────────────────────────────────────

async def analyze_files_timeline_task(project_path: str) -> None:
    """POST /api/v1/project/initialize 에서 BackgroundTasks 로 실행된다."""

    # ── 사전 준비: 진짜 repo_id 동적 확보 (파일 루프 전 1회) ──────────────────
    try:
        repo_id: int = await _get_or_create_repo_id(project_path)
    except Exception as exc:
        logger.exception("[task] repo_id 확보 실패, 분석 중단 — %s : %s",
                         project_path, exc)
        return

    files = _get_tracked_files(project_path)
    head  = _get_repo_head(project_path)

    logger.info("[task] ▶ 분석 시작 — repo_id=%d  파일 %d개  HEAD=%s",
                repo_id, len(files), head)

    counters: dict[str, int] = {
        "inserted": 0, "updated": 0, "skipped": 0, "failed": 0
    }

    for idx, file_path in enumerate(files, start=1):
        logger.info("[task] [%d/%d] %s", idx, len(files), file_path)

        result = await _process_file(project_path, file_path, repo_id)

        if result in ("inserted", "updated"):
            counters[result] += 1
        elif result == "failed":
            counters["failed"] += 1
        else:
            counters["skipped"] += 1

    logger.info(
        "[task] ■ 완료 — inserted=%d  updated=%d  skipped=%d  failed=%d",
        counters["inserted"], counters["updated"],
        counters["skipped"],  counters["failed"],
    )
