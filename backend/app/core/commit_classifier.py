"""커밋 메시지 분류기 — 블레임/타임라인 공유.

타임라인의 캐시 키 계산(TIMELINE_OPTIMIZATION_PLAN.md §2 C)과
블레임의 노이즈 우회(DEVELOPMENT_GUIDE.md §10 P1)가
같은 정의를 보고 동작하도록 한 곳에서 관리한다.

계약(변경 금지): 모듈 경로 app.core.commit_classifier,
공개 심볼 classify_commit / SKIP_TYPES / filter_meaningful.
"""

import re
from typing import Iterable

SKIP_TYPES: frozenset[str] = frozenset({"test", "chore", "docs"})

_COMMIT_RE = re.compile(r"^(?P<type>\w+)(?:\[(?P<domain>[^\]]+)\])?:\s*(?P<subject>.+)$")


def classify_commit(commit: dict) -> dict:
    """{type, domain, subject}를 추가해 돌려준다. 매칭 실패 시 type='other'."""
    m = _COMMIT_RE.match(commit.get("subject") or commit.get("message", ""))
    if not m:
        return {**commit, "type": "other", "domain": None}
    return {**commit, "type": m.group("type").lower(), "domain": m.group("domain")}


def filter_meaningful(commits: Iterable[dict]) -> list[dict]:
    """SKIP_TYPES(test/chore/docs)를 제외한 커밋만 반환. 전부 노이즈면 전체를 그대로 돌려준다."""
    classified = [classify_commit(c) for c in commits]
    meaningful = [c for c in classified if c["type"] not in SKIP_TYPES]
    return meaningful or classified
