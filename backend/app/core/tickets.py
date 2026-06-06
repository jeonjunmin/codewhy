"""이슈 트래커 키(티켓) 추출 — 세 기능 공통 유틸.

티켓 번호(예: PAY-2041)는 commits.ticket 에 저장되어 역추적에서 문서와 코드를 잇는 1차 다리가 된다.
블레임·타임라인·문서 업로드가 모두 같은 규칙으로 티켓을 뽑도록 한곳에 모은다.
"""

import re

# 이슈 트래커 키 패턴 — 예: PAY-2041, KYC-12
_TICKET_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


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
