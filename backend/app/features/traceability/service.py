"""Requirement Trace 비즈니스 로직.

코드 라인 → blamed 커밋 → PR → GitHub Issue → 첨부 파일 경로로 연관 기획 문서를 찾는다.

추적 전략 (신뢰도 높은 순):
  1. issue    : commit → PR 본문 → Issue 직접 연결, 첨부 파일 있음 (확정)
  2. ticket   : 커밋 메시지의 티켓 번호(PAY-2041)로 GitHub Issue 검색 (높음)
  3. semantic : 커밋 키워드로 GitHub Issues 검색 (추정)

각 결과에 matchType 과 confidence 를 실어 UI 가 신뢰도를 구분 표시한다.

설계 원칙: 모든 외부 연동 실패는 빈 결과 → 로컬에서 절대 깨지지 않는다.

👤 담당: 개발자 C
"""

from app.core import vcs
from app.core.vcs import Issue


_MAX_EXCERPT_CHARS = 300


def trace(commit_hash: str, commit_message: str, *, branch: str, remote) -> list[dict]:
    """blamed 커밋을 기준으로 연관 기획 문서(GitHub Issue) 목록을 반환한다.

    git 실행은 확장이 끝냈고, 여기서는 받은 데이터(커밋 해시/메시지/브랜치)와
    파싱된 remote 로 GitHub API 만 조회한다.
    """
    issues, match_type = vcs.find_issues_for_remote(remote, commit_hash, commit_message, branch)
    if not issues:
        return []

    return _format_results(issues, match_type)


def _format_results(issues: list[Issue], match_type: str) -> list[dict]:
    """Issue 목록을 TraceResponse DocumentMatch 형식으로 변환한다."""
    results: list[dict] = []

    for issue in issues:
        excerpt = _make_excerpt(issue.body)

        if issue.attachments:
            # 첨부 파일이 있으면 각 첨부를 별도 항목으로 (첨부 URL → 직접 열기)
            for att in issue.attachments:
                results.append({
                    "title": att.label or f"Issue #{issue.number}: {issue.title}",
                    "url": att.url,
                    "matchType": match_type,
                    "confidence": None if match_type == "issue" else 0.8 if match_type == "ticket" else 0.5,
                    "excerpt": excerpt,
                })
        else:
            # 첨부 없으면 Issue 자체를 항목으로
            results.append({
                "title": f"Issue #{issue.number}: {issue.title}" if issue.title else f"Issue #{issue.number}",
                "url": issue.url,
                "matchType": match_type,
                "confidence": None if match_type == "issue" else 0.8 if match_type == "ticket" else 0.5,
                "excerpt": excerpt,
            })

    return results


def _make_excerpt(body: str) -> str | None:
    if not body or not body.strip():
        return None
    stripped = body.strip()
    if len(stripped) > _MAX_EXCERPT_CHARS:
        return stripped[:_MAX_EXCERPT_CHARS] + "…"
    return stripped
