"""Git 저수준 헬퍼.

세 기능이 공통으로 쓰는 git 호출만 모은다. 비즈니스 로직은 각 기능의 service.py 에서 다룬다.
"""

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone


class BlameUnavailable(Exception):
    """blame 을 낼 수 없는 '정상적인' 상황 — 시스템 오류가 아니다.

    대표 케이스: 아직 커밋되지 않은(untracked) 파일이라 HEAD 에 경로가 없음.
    이 예외는 500 이 아니라 사용자에게 보여줄 안내로 변환되어야 한다.
    reason: 'uncommitted' | 'no_history'
    """

    def __init__(self, message: str, reason: str = "no_history"):
        super().__init__(message)
        self.reason = reason


def _is_tracked(repo_path: str, file_path: str) -> bool:
    """파일이 git 에 추적(커밋 이력 보유)되는지 여부."""
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", file_path],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.returncode == 0


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
    """특정 라인의 마지막 커밋 정보와 diff 를 반환한다.

    아직 커밋되지 않은 파일/라인은 HEAD 에 경로가 없어 git blame 이 exit 128 로 실패한다.
    이는 시스템 오류가 아니라 '추적할 이력이 없는' 정상 상황이므로 BlameUnavailable 로 변환한다.
    """
    try:
        blame_out = subprocess.check_output(
            ["git", "blame", "-L", f"{line},{line}", "--porcelain", file_path],
            cwd=repo_path,
            text=True,
            encoding="utf-8",
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as e:
        if not _is_tracked(repo_path, file_path):
            raise BlameUnavailable(
                f"아직 커밋되지 않은 파일입니다: {file_path}", reason="uncommitted"
            ) from e
        # 추적은 되지만 blame 실패(예: 라인 범위 초과) — 역시 분석 불가
        raise BlameUnavailable(
            (e.stderr or "").strip() or "blame 을 가져올 수 없습니다", reason="no_history"
        ) from e

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


def get_commit_info(repo_path: str, file_path: str, commit_hash: str) -> BlameInfo:
    """임의 커밋 하나의 메타 + 해당 파일 diff 를 BlameInfo 로 조립한다.

    get_blame_info(특정 '라인'의 현재 blame)와 달리, '라인 수정 이력'에서 고른 과거 커밋
    해시로 직접 BlameInfo 를 만든다. 사이드바 이력 항목을 펼칠 때 그 커밋의 변경 사유를
    지연 생성(/api/blame/reason)하는 데 쓴다.

    해시가 유효하지 않으면(잘린 이력/리베이스 등) BlameUnavailable 로 변환한다.
    """
    try:
        meta = subprocess.check_output(
            ["git", "show", "-s", "--format=%H|%an|%ad", "--date=short", commit_hash],
            cwd=repo_path,
            text=True,
            encoding="utf-8",
            stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError as e:
        raise BlameUnavailable(
            (e.stderr or "").strip() or f"커밋을 찾을 수 없습니다: {commit_hash}",
            reason="no_history",
        ) from e

    parts = meta.split("|", 2)
    commit_full = parts[0] if parts and parts[0] else commit_hash
    author = parts[1] if len(parts) > 1 else ""
    date = parts[2] if len(parts) > 2 else ""
    message = _get_commit_message(repo_path, commit_hash)
    diff = _get_commit_diff(repo_path, commit_hash, file_path)
    added, removed = _get_commit_numstat(repo_path, commit_hash, file_path)

    return BlameInfo(
        commit_hash=commit_full,
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


def get_line_history(
    repo_path: str, file_path: str, line: int, max_count: int = 8
) -> list[dict]:
    """특정 라인이 '실제로 바뀐' 커밋들의 이력을 최신순으로 반환한다.

    파일 전체 이력(get_file_log)과 달리, `git log -L<line>,<line>:<file>` 로
    해당 한 줄의 변천만 추린다 — 그 줄을 건드리지 않은 커밋은 빠진다.
    `-s`(--no-patch)로 diff hunk 는 억제하고 메타데이터만 받는다.

    사이드바 '라인 수정 이력' 섹션에 쓴다.
    blame 과 달리 줄이 막 추가돼 이력이 한 건뿐이어도 빈 리스트가 아니라 그 한 건을 준다.
    실패(미커밋/범위 초과 등)하면 빈 리스트 — 호출부에서 섹션을 숨긴다.

    반환: [{"hash","author","date","subject"}, ...]  (최신순, 최대 max_count 건)
    """
    try:
        out = subprocess.check_output(
            [
                "git", "log", "-s",
                f"-L{line},{line}:{file_path}",
                f"-n{max_count}",
                "--format=%H|%an|%ad|%s",
                "--date=short",
            ],
            cwd=repo_path,
            text=True,
            encoding="utf-8",
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError:
        return []

    commits: list[dict] = []
    for raw in out.strip().splitlines():
        parts = raw.split("|", 3)
        if len(parts) == 4:
            commits.append(
                {"hash": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]}
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
