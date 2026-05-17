"""Git 저수준 헬퍼.

세 기능이 공통으로 쓰는 git 호출만 모은다. 비즈니스 로직은 각 기능의 service.py 에서 다룬다.
"""

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class BlameInfo:
    commit_hash: str
    author: str
    date: str
    message: str
    diff: str


def get_blame_info(repo_path: str, file_path: str, line: int) -> BlameInfo:
    """특정 라인의 마지막 커밋 정보와 diff 를 반환한다."""
    blame_out = subprocess.check_output(
        ["git", "blame", "-L", f"{line},{line}", "--porcelain", file_path],
        cwd=repo_path,
        text=True,
        encoding="utf-8",
    )

    lines = blame_out.splitlines()
    commit_hash = lines[0].split()[0]
    author = next(
        (l.removeprefix("author ") for l in lines if l.startswith("author ")),
        "",
    )
    raw_ts = next(
        (l.removeprefix("author-time ") for l in lines if l.startswith("author-time ")),
        "",
    )
    try:
        date = datetime.fromtimestamp(int(raw_ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        date = raw_ts  # 파싱 실패 시 원본 그대로
    message = _get_commit_message(repo_path, commit_hash)
    diff = _get_commit_diff(repo_path, commit_hash, file_path)

    return BlameInfo(
        commit_hash=commit_hash,
        author=author,
        date=date,
        message=message,
        diff=diff,
    )


def get_file_log(repo_path: str, file_path: str) -> list[dict]:
    """파일의 커밋 이력(해시/작성자/날짜/제목)을 반환한다."""
    out = subprocess.check_output(
        ["git", "log", "--follow", "--format=%H|%an|%ad|%s", "--date=short", file_path],
        cwd=repo_path,
        text=True,
        encoding="utf-8",
    )
    commits = []
    for line in out.strip().splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            commits.append(
                {
                    "hash": parts[0],
                    "author": parts[1],
                    "date": parts[2],
                    "subject": parts[3],
                }
            )
    return commits


def _get_commit_message(repo_path: str, commit_hash: str) -> str:
    return subprocess.check_output(
        ["git", "log", "-1", "--format=%B", commit_hash],
        cwd=repo_path,
        text=True,
        encoding="utf-8",
    ).strip()


def _get_commit_diff(repo_path: str, commit_hash: str, file_path: str) -> str:
    return subprocess.check_output(
        ["git", "show", "--stat", commit_hash, "--", file_path],
        cwd=repo_path,
        text=True,
        encoding="utf-8",
    ).strip()
