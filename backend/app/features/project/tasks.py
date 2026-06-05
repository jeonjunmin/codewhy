"""프로젝트 파일별 타임라인 분석 태스크 — 성능 최적화 버전.

🚀 성능 개선 3종:
  1. Git Diff 만 사용 (전체 소스 대신 git show HEAD -- file)
  2. asyncio.gather 병렬 처리 (Bedrock 동시 N개 호출, 세마포어 제한)
  3. 벌크 커밋 (파일별 commit 대신 전체 완료 후 1회 commit)

흐름:
  ① _get_or_create_repo_id()   — 1회, repo_id 확보
  ② asyncio.gather(분석 태스크 × 파일 수)   — 병렬 실행 (DB 쓰기 없음)
       각 태스크: git diff 수집 → 캐시 조회 → LangGraph/Bedrock
  ③ 벌크 쓰기 (단일 세션)
       files / timeline_summary_cache / timeline_summaries 모두 처리
       → await db.commit()  1회
"""

import asyncio
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.ai.timeline_file_graph import run_file_timeline_graph
from app.core import git
from app.db.models import File, Repository, TimelineSummary, TimelineSummaryCache
from app.db.postgres import AsyncSessionLocal

logger = logging.getLogger(__name__)

# Bedrock 동시 호출 제한 — rate limit 보호
_BEDROCK_SEM = asyncio.Semaphore(10)

SKIP_TYPES: frozenset[str] = frozenset({"test", "chore"})

_SOURCE_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx",
    ".java", ".kt", ".go", ".rs", ".swift",
    ".c", ".cpp", ".cs",
})

_COMMIT_RE = re.compile(r"^(?P<type>\w+)(?:\[(?P<domain>[^\]]+)\])?:")


# ── 분석 결과 데이터 클래스 ────────────────────────────────────────────────────

@dataclass
class _FileAnalysis:
    """파일 하나의 분석 결과. DB 쓰기는 포함하지 않는다."""
    file_path:    str
    status:       str          # "skip_*" | "failed" | "ready"
    latest_hash:  str  = ""
    commit_type:  str  = ""
    commit_domain: str = ""
    summary:      str  = ""
    milestones:   dict = field(default_factory=dict)
    cache_data:   dict = field(default_factory=dict)
    cache_exists: bool = False  # True → UPDATE, False → INSERT
    file_id:      int | None = None  # None → files 테이블 INSERT 필요


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


def _get_file_diff(repo_path: str, file_path: str) -> str:
    """git show HEAD -- file_path 로 최신 커밋의 diff 를 반환한다.

    전체 소스 대신 변경점만 Bedrock 에 전달 → 토큰 절감 + 속도 향상.
    diff 가 없으면 빈 문자열 반환.
    """
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
    """repositories.identifier 로 repo_id 를 가져오거나 INSERT 한다 (1회 호출)."""
    folder_name = os.path.basename(os.path.normpath(project_path))

    async with AsyncSessionLocal() as db:
        repo: Repository | None = await db.scalar(
            select(Repository).where(Repository.identifier == folder_name)
        )

        if repo is not None:
            logger.info("[task] repo 기존 — repo_id=%d  identifier=%s", repo.id, folder_name)
            return repo.id

        new_repo = Repository(
            identifier = folder_name,
            name       = folder_name,
            created_at = datetime.utcnow(),
        )
        db.add(new_repo)
        await db.flush()
        await db.refresh(new_repo)
        await db.commit()
        logger.info("[task] repo INSERT — repo_id=%d  identifier=%s", new_repo.id, folder_name)
        return new_repo.id


# ── 파일 단위 분석 (DB 쓰기 없음, 병렬 실행 단위) ────────────────────────────

async def _analyze_file(
    repo_path: str,
    file_path: str,
    repo_id:   int,
) -> _FileAnalysis:
    """파일 하나를 분석하고 결과 객체를 반환한다.

    DB 쓰기를 전혀 하지 않으므로 asyncio.gather 로 안전하게 병렬 실행 가능.
    """
    async with _BEDROCK_SEM:   # Bedrock 동시 호출 10개 제한

        # ── git log (최신 커밋 메타) ──────────────────────────────────────────
        try:
            commits: list[dict] = await asyncio.to_thread(
                git.get_file_log, repo_path, file_path
            )
        except Exception as exc:
            logger.warning("[task]   git log 실패 — %s : %s", file_path, exc)
            return _FileAnalysis(file_path=file_path, status="skip_no_commits")

        if not commits:
            return _FileAnalysis(file_path=file_path, status="skip_no_commits")

        latest_hash    = commits[0]["hash"]
        latest_subject = commits[0].get("subject", "")

        # ── 캐시 조회 + file_id 조회 (동시 읽기) ────────────────────────────
        async def _read_cache() -> TimelineSummaryCache | None:
            async with AsyncSessionLocal() as db:
                return await db.scalar(
                    select(TimelineSummaryCache).where(
                        TimelineSummaryCache.repo_path == repo_path,
                        TimelineSummaryCache.file_path == file_path,
                    )
                )

        async def _read_file_id() -> int | None:
            async with AsyncSessionLocal() as db:
                row: File | None = await db.scalar(
                    select(File).where(
                        File.repo_id   == repo_id,
                        File.file_path == file_path,
                    )
                )
                return row.id if row else None

        cache_row, file_id = await asyncio.gather(_read_cache(), _read_file_id())

        # ── 해시 비교 → skip ──────────────────────────────────────────────────
        if cache_row is not None:
            if (cache_row.data or {}).get("commit_hash") == latest_hash:
                logger.info("[task]   ⏭  skip_hash — %s", file_path)
                return _FileAnalysis(file_path=file_path, status="skip_hash")

        # ── 커밋 파싱 + type skip ─────────────────────────────────────────────
        parsed        = _parse_commit(latest_subject)
        commit_type   = parsed["type"]
        commit_domain = parsed["domain"]

        if commit_type in SKIP_TYPES:
            logger.info("[task]   ⏭  skip_type='%s' — %s", commit_type, file_path)
            return _FileAnalysis(file_path=file_path, status="skip_type")

        # ── git diff (전체 소스 대신 변경점만) ───────────────────────────────
        diff_text = await asyncio.to_thread(_get_file_diff, repo_path, file_path)
        bedrock_input = diff_text if diff_text.strip() else _format_commits(commits)

        # ── LangGraph ainvoke() → Bedrock ────────────────────────────────────
        logger.info("[task]   🤖 LangGraph — %s  diff=%d자", file_path, len(bedrock_input))

        try:
            summary: str = await run_file_timeline_graph(
                file_path    = file_path,
                commits_text = bedrock_input,
                commit_type  = commit_type,
                commit_domain= commit_domain,
            )
            logger.info("[task]   ✅ Bedrock — %s  요약 %d자", file_path, len(summary))
        except Exception as exc:
            logger.exception("[task]   ❌ LangGraph 실패 — %s : %s", file_path, exc)
            return _FileAnalysis(file_path=file_path, status="failed")

        return _FileAnalysis(
            file_path    = file_path,
            status       = "ready",
            latest_hash  = latest_hash,
            commit_type  = commit_type,
            commit_domain= commit_domain,
            summary      = summary,
            milestones   = {"type": commit_type, "domain": commit_domain},
            cache_data   = {
                "commit_hash": latest_hash,
                "type":        commit_type,
                "domain":      commit_domain,
            },
            cache_exists = (cache_row is not None),
            file_id      = file_id,
        )


# ── 벌크 DB 쓰기 (단일 세션 + 1회 commit) ─────────────────────────────────────

async def _bulk_save(
    repo_path: str,
    repo_id:   int,
    analyses:  list[_FileAnalysis],
) -> dict[str, int]:
    """모든 파일의 분석 결과를 단일 세션에서 처리하고 commit 을 1회만 호출한다."""

    ready = [a for a in analyses if a.status == "ready"]
    if not ready:
        return {"inserted": 0, "updated": 0}

    counters = {"inserted": 0, "updated": 0}

    async with AsyncSessionLocal() as db:
        for a in ready:

            # ── files: SELECT or INSERT + flush → file_id 확보 ───────────────
            if a.file_id is None:
                new_file = File(repo_id=repo_id, file_path=a.file_path)
                db.add(new_file)
                await db.flush()            # 시퀀스 id 발급 (commit 전)
                await db.refresh(new_file)
                file_id: int = new_file.id
                logger.info("[task]   files INSERT — file_id=%d  %s", file_id, a.file_path)
            else:
                file_id = a.file_id

            # ── timeline_summary_cache UPSERT ────────────────────────────────
            if a.cache_exists:
                cache_row: TimelineSummaryCache | None = await db.scalar(
                    select(TimelineSummaryCache).where(
                        TimelineSummaryCache.repo_path == repo_path,
                        TimelineSummaryCache.file_path == a.file_path,
                    )
                )
                if cache_row:
                    cache_row.data = a.cache_data
            else:
                db.add(TimelineSummaryCache(
                    repo_path  = repo_path,
                    file_path  = a.file_path,
                    data       = a.cache_data,
                    created_at = datetime.utcnow(),
                ))

            # ── timeline_summaries INSERT / UPDATE ────────────────────────────
            existing: TimelineSummary | None = await db.scalar(
                select(TimelineSummary).where(TimelineSummary.file_id == file_id)
            )

            if existing is None:
                db.add(TimelineSummary(
                    file_id         = file_id,
                    commit_set_hash = a.latest_hash,
                    summary         = a.summary,
                    milestones      = a.milestones,
                    created_at      = datetime.utcnow(),
                ))
                counters["inserted"] += 1
            else:
                existing.commit_set_hash = a.latest_hash
                existing.summary         = a.summary
                existing.milestones      = a.milestones
                counters["updated"] += 1

        # ── 모든 파일 처리 완료 후 1회 커밋 ──────────────────────────────────
        logger.info("[task] 💾 벌크 commit — inserted=%d  updated=%d",
                    counters["inserted"], counters["updated"])
        await db.commit()

    return counters


# ── 메인 태스크 ───────────────────────────────────────────────────────────────

async def analyze_files_timeline_task(project_path: str) -> None:
    """POST /api/v1/project/initialize 에서 BackgroundTasks 로 실행된다."""

    # ── 1. repo_id 동적 확보 (1회) ────────────────────────────────────────────
    try:
        repo_id: int = await _get_or_create_repo_id(project_path)
    except Exception as exc:
        logger.exception("[task] repo_id 확보 실패, 중단 — %s", exc)
        return

    files = _get_tracked_files(project_path)
    head  = _get_repo_head(project_path)

    logger.info("[task] ▶ 병렬 분석 시작 — repo_id=%d  파일 %d개  HEAD=%s",
                repo_id, len(files), head)

    # ── 2. 병렬 분석 (Bedrock 동시 최대 10개, DB 쓰기 없음) ──────────────────
    analysis_tasks = [
        _analyze_file(project_path, fp, repo_id)
        for fp in files
    ]
    raw_results = await asyncio.gather(*analysis_tasks, return_exceptions=True)

    # 예외 발생 건 처리
    analyses: list[_FileAnalysis] = []
    for fp, res in zip(files, raw_results):
        if isinstance(res, Exception):
            logger.exception("[task] 분석 예외 — %s : %s", fp, res)
            analyses.append(_FileAnalysis(file_path=fp, status="failed"))
        else:
            analyses.append(res)

    # 통계 집계
    status_counts: dict[str, int] = {}
    for a in analyses:
        status_counts[a.status] = status_counts.get(a.status, 0) + 1

    ready_count = status_counts.get("ready", 0)
    logger.info("[task] 분석 완료 — %s  (ready=%d 건 저장 예정)",
                status_counts, ready_count)

    if ready_count == 0:
        logger.info("[task] ■ 저장할 데이터 없음, 종료")
        return

    # ── 3. 벌크 DB 저장 (단일 세션 + 1회 commit) ─────────────────────────────
    counters = await _bulk_save(project_path, repo_id, analyses)

    logger.info(
        "[task] ■ 완료 — inserted=%d  updated=%d  skipped=%d  failed=%d",
        counters["inserted"],
        counters["updated"],
        sum(v for k, v in status_counts.items() if k.startswith("skip")),
        status_counts.get("failed", 0),
    )
