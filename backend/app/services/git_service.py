import subprocess
from dataclasses import dataclass


@dataclass
class BlameInfo:
    commit_hash: str
    author: str
    date: str
    message: str
    diff: str


def get_blame_info(repo_path: str, file_path: str, line: int) -> BlameInfo:
    """특정 라인의 마지막 커밋 정보와 diff를 반환한다."""
    blame_out = subprocess.check_output(
        ["git", "blame", "-L", f"{line},{line}", "--porcelain", file_path],
        cwd=repo_path,
        text=True,
    )

    lines = blame_out.splitlines()
    commit_hash = lines[0].split()[0]
    author = next((l.removeprefix("author ") for l in lines if l.startswith("author ")), "")
    date = next((l.removeprefix("author-time ") for l in lines if l.startswith("author-time ")), "")
    message = _get_commit_message(repo_path, commit_hash)
    diff = _get_commit_diff(repo_path, commit_hash, file_path)

    return BlameInfo(commit_hash=commit_hash, author=author, date=date, message=message, diff=diff)


def get_file_log(repo_path: str, file_path: str) -> list[dict]:
    """파일의 커밋 이력을 반환한다."""
    out = subprocess.check_output(
        ["git", "log", "--follow", "--format=%H|%an|%ad|%s", "--date=short", file_path],
        cwd=repo_path,
        text=True,
    )
    commits = []
    for line in out.strip().splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            commits.append({"hash": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]})
    return commits


def _get_commit_message(repo_path: str, commit_hash: str) -> str:
    return subprocess.check_output(
        ["git", "log", "-1", "--format=%B", commit_hash],
        cwd=repo_path,
        text=True,
    ).strip()


def _get_commit_diff(repo_path: str, commit_hash: str, file_path: str) -> str:
    return subprocess.check_output(
        ["git", "show", "--stat", commit_hash, "--", file_path],
        cwd=repo_path,
        text=True,
    ).strip()
