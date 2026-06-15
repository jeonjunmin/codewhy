"""이슈 트래커 키(티켓) 추출 — 세 기능 공통 유틸.

티켓 번호(예: PAY-2041)는 commits.ticket 에 저장되어 역추적에서 문서와 코드를 잇는 1차 다리가 된다.
블레임·타임라인·문서 업로드가 모두 같은 규칙으로 티켓을 뽑도록 한곳에 모은다.
"""

import re

# 이슈 트래커 키 패턴 — 예: PAY-2041, KYC-12
_TICKET_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")

# GitHub 이슈/PR 참조 패턴 — 예: #12, #2041
_ISSUE_RE = re.compile(r"#(\d+)")


def extract_issue_numbers(message: str) -> list[int]:
    """커밋 메시지에서 GitHub 이슈 참조(#N)를 중복 없이(순서 보존) 추출한다.

    세 기능 공통 유틸 — 블레임의 라인 이슈 롤업(A), 타임라인 마일스톤 태깅(B),
    이슈 메타 해석(C)이 같은 규칙으로 이슈 번호를 뽑도록 한곳에 둔다.
    지라 티켓(PAY-2041)은 여기서 세지 않는다 — 그건 extract_ticket(s) 담당.
    """
    seen: set[int] = set()
    out: list[int] = []
    for m in _ISSUE_RE.finditer(message or ""):
        n = int(m.group(1))
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def extract_ticket(commit_message: str, branch: str = "") -> str | None:
    """커밋 메시지 또는 브랜치명에서 이슈 키를 추출한다(커밋 메시지 우선)."""
    for text in (commit_message, branch):
        m = _TICKET_RE.search(text or "")
        if m:
            return m.group(1)
    return None


def extract_tickets(text: str) -> list[str]:
    """텍스트에서 모든 이슈 키를 중복 없이(순서 보존) 추출한다. 문서 자동 태깅용."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _TICKET_RE.finditer(text or ""):
        key = m.group(1)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out
