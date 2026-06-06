"""타임라인 분석 백그라운드 태스크 — 프로젝트 파일 전체 순회.

BackgroundTasks 또는 직접 await 로 호출한다.

흐름:
  1. git ls-files 로 소스 파일 목록 수집
  2. git rev-parse HEAD 로 commit_set_hash 확보
  3. 각 파일별 analyze_and_save_file_summary() 호출
     → file_summary.py 의 전체 Skip/INSERT/UPDATE 파이프라인 실행
"""

import logging
import subprocess
from pathlib import Path

from app.features.timeline.file_summary import analyze_and_save_file_summary

logger = logging.getLogger(__name__)

# 분석 대상 확장자 (필요에 따라 추가)
_SOURCE_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx",
    ".java", ".kt", ".go", ".rs", ".swift",
})


# ── git 헬퍼 ──────────────────────────────────────────────────────────────────

def _get_tracked_files(repo_path: str) -> list[str]:
    """git ls-files 로 추적 중인 소스 파일 목록을 반환한다."""
    try:
        out = subprocess.check_output(
            ["git", "ls-files"],
            cwd=repo_path, text=True, encoding="utf-8", timeout=15,
        )
        return [
            f for f in out.strip().splitlines()
            if Path(f).suffix in _SOURCE_EXTENSIONS
        ]
    except Exception as exc:
        logger.warning("[tasks] git ls-files 실패 — %s : %s", repo_path, exc)
        return []


def _get_repo_head(repo_path: str) -> str:
    """git rev-parse HEAD 로 현재 최신 커밋 해시를 반환한다."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path, text=True, encoding="utf-8", timeout=5,
        ).strip()
    except Exception:
        return "unknown"


# ── 메인 태스크 ───────────────────────────────────────────────────────────────

async def analyze_all_project_files(repo_path: str) -> dict[str, int]:
    """프로젝트의 모든 소스 파일을 순회하며 분석·저장한다.

    Args:
        repo_path: git 루트 절대 경로

    Returns:
        {"inserted": N, "updated": N, "skipped": N, "failed": N}
    """
    files           = _get_tracked_files(repo_path)
    commit_set_hash = _get_repo_head(repo_path)

    logger.info("[tasks] 분석 시작 — repo=%s  파일 %d개  HEAD=%s",
                repo_path, len(files), commit_set_hash)

    results: dict[str, int] = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}

    for file_path in files:
        status = await analyze_and_save_file_summary(
            repo_path=repo_path,
            file_path=file_path,
            commit_set_hash=commit_set_hash,
        )

        if status in ("inserted", "updated"):
            results[status] += 1
        elif status == "failed":
            results["failed"] += 1
        else:
            results["skipped"] += 1

        logger.info("[tasks] %s → %s", file_path, status)

    logger.info("[tasks] 완료 — %s", results)
    return results
