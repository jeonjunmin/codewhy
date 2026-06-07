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
    added: int = 0    # 해당 파일에서 이 커밋이 추가한 라인 수
    removed: int = 0  # 해당 파일에서 이 커밋이 삭제한 라인 수


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
    added, removed = _get_commit_numstat(repo_path, commit_hash, file_path)

    return BlameInfo(
        commit_hash=commit_hash,
        author=author,
        date=date,
        message=message,
        diff=diff,
        added=added,
        removed=removed,
    )


def get_current_branch(repo_path: str) -> str:
    """현재 체크아웃된 브랜치명. detached HEAD 등 실패 시 빈 문자열."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            text=True,
            encoding="utf-8",
        ).strip()
    except subprocess.CalledProcessError:
        return ""


def find_followup_commits(repo_path: str, ticket: str, exclude_hash: str = "") -> list[dict]:
    """같은 티켓(예: PAY-2041)을 참조하는 다른 커밋들.

    '이 변경과 함께 일어난 일'의 후속 변경 행(예: KYC 감사 로그 후속 추가)에 쓴다.
    티켓이 없으면 빈 리스트.
    """
    if not ticket:
        return []
    try:
        out = subprocess.check_output(
            ["git", "log", f"--grep={ticket}", "--format=%H|%an|%ad|%s", "--date=short"],
            cwd=repo_path,
            text=True,
            encoding="utf-8",
        )
    except subprocess.CalledProcessError:
        return []

    commits = []
    for line in out.strip().splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        if exclude_hash and parts[0].startswith(exclude_hash):
            continue  # 블레임 대상 커밋 자신은 제외
        commits.append(
            {"hash": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]}
        )
    return commits


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


def get_repo_log(repo_path: str, since: str = "", limit: int = 0) -> list[dict]:
    """레포 전체 커밋 이력을 변경 파일 목록과 함께 반환한다(브라운필드 백필용).

    파일 단위(get_file_log)와 달리 레포 전체를 한 번에 훑는다. 커밋마다 numstat 으로
    바뀐 파일 경로/추가·삭제 라인을 함께 모아, 백필이 커밋↔문서 시맨틱 매칭에 쓸
    조회 텍스트(메시지 + 파일명)를 구성할 수 있게 한다.

    반환: [{"hash","author","author_email","date","subject",
            "files":[{"path","added","removed"}, ...]}, ...]  (최신순)
    """
    cmd = ["git", "log", "--numstat", "--date=short", "--format=@@@%H|%an|%ae|%ad|%s"]
    if since:
        cmd.append(f"--since={since}")
    if limit:
        cmd.append(f"-n{limit}")

    out = subprocess.check_output(cmd, cwd=repo_path, text=True, encoding="utf-8")

    commits: list[dict] = []
    current: dict | None = None
    for line in out.splitlines():
        if line.startswith("@@@"):
            parts = line[3:].split("|", 4)
            if len(parts) != 5:
                current = None
                continue
            current = {
                "hash": parts[0],
                "author": parts[1],
                "author_email": parts[2],
                "date": parts[3],
                "subject": parts[4],
                "files": [],
            }
            commits.append(current)
        elif current is not None and line.strip():
            cols = line.split("\t")
            if len(cols) >= 3:
                added = int(cols[0]) if cols[0].isdigit() else 0
                removed = int(cols[1]) if cols[1].isdigit() else 0
                current["files"].append({"path": cols[2], "added": added, "removed": removed})
    return commits


def _get_commit_message(repo_path: str, commit_hash: str) -> str:
    return subprocess.check_output(
        ["git", "log", "-1", "--format=%B", commit_hash],
        cwd=repo_path,
        text=True,
        encoding="utf-8",
    ).strip()


def _get_commit_diff(repo_path: str, commit_hash: str, file_path: str) -> str:
    """커밋의 파일별 변경 diff(stat+patch).

    Merge 커밋은 `git show` 기본 동작이 'combined diff'라 단일 파일 patch 가 비어 나온다.
    `-m --first-parent` 로 mainline 부모 기준 일반 diff 를 강제하면 merge 도 동일한 형식의
    hunk 를 얻는다(비-머지 커밋에는 영향 없음).
    """
    return subprocess.check_output(
        ["git", "show", "-p", "--stat", "-m", "--first-parent", commit_hash, "--", file_path],
        cwd=repo_path,
        text=True,
        encoding="utf-8",
    ).strip()


def _get_commit_numstat(repo_path: str, commit_hash: str, file_path: str) -> tuple[int, int]:
    """이 커밋이 해당 파일에 가한 추가/삭제 라인 수.

    `git blame` 은 rename 을 따라가므로, 라인을 만든 커밋이 그 파일을 '지금 경로'가 아닌
    '과거 경로'에서 건드렸을 수 있다. 그래서 `git show <hash> -- <현재경로>` 는 빈 결과가 나기 쉽다.
    rename 을 따라가는 `git log --follow --numstat` 으로 파일 이력을 훑어 해당 커밋의 행을 찾는다.

    numstat 형식: 파일당 `추가\\t삭제\\t경로`. 바이너리는 '-' → 0 으로 처리.
    """
    out = subprocess.check_output(
        ["git", "log", "--follow", "--numstat", "--format=__%H", "--", file_path],
        cwd=repo_path,
        text=True,
        encoding="utf-8",
    )
    in_target = False
    for line in out.splitlines():
        if line.startswith("__"):
            h = line[2:]
            in_target = bool(h) and (h.startswith(commit_hash) or commit_hash.startswith(h))
            continue
        if in_target:
            parts = line.split("\t")
            if len(parts) >= 2:
                added = int(parts[0]) if parts[0].isdigit() else 0
                removed = int(parts[1]) if parts[1].isdigit() else 0
                return added, removed
    return 0, 0
