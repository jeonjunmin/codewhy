"""Context Blame 비즈니스 로직.

git 으로 라인 단위 마지막 커밋(diff + 커밋 메시지)을 가져오고,
커밋이 속한 PR 본문이 가리키는 GitHub Issue 와 첨부 문서 링크를 모아,
코드 + 커밋 메시지 + 이슈 본문/첨부를 Bedrock 에 한꺼번에 넣어 "진짜 변경 이유"를 추론한다.
여기에 호스팅(PR) 맥락과 후속 커밋을 더해 사이드바 디자인의 모든 필드를 채운다.

흐름:
  1. git.get_blame_info  — diff + 커밋 메시지 + 변경 라인 수 추출
  2. vcs.find_pr_for_commit / find_issues_from_pr_body
                          — PR 본문에서 연결 이슈 파싱 + 이슈 본문/첨부 수집
  3. _explain_blame       — 코드 + 커밋 + 이슈 맥락을 Bedrock Converse 로 정렬
  4. followups            — 같은 티켓 후속 커밋으로 '함께 일어난 일' 조립

👤 담당: 개발자 A
"""

import os
import re

from app.core import git, vcs
from app.core.ai_client import call_bedrock
from app.core.config import get_team_map
from app.core.tickets import extract_ticket
from app.core.vcs import Issue, PullRequest

_SYSTEM_PROMPT = (
    "당신은 코드 변경의 '기획 의도'를 설명하는 도우미입니다. "
    "git 커밋 메시지의 기술적 표현 뒤에 숨은, 연관 이슈와 첨부된 요구사항 문서가 알려주는 "
    "진짜 이유를 추론해 비개발자도 이해할 수 있는 한국어로 설명하세요."
)

# Bedrock 에 보낼 diff 의 문자 수 상한 — 거대 커밋의 토큰 폭발(비용·지연)을 막는다.
_MAX_DIFF_CHARS = 2000

# 이슈 본문 발췌의 상한 — 긴 PRD/요구사항이 통째로 들어가 토큰을 잡아먹지 않도록.
_MAX_ISSUE_BODY_CHARS = 800

# 후속 변경을 'security' 로 분류할 도메인 신호어
_SECURITY_TERMS = ("KYC", "감사", "audit", "보안", "security", "권한", "auth")


def analyze_blame(
    repo_path: str, file_path: str, line: int, info: git.BlameInfo | None = None
) -> dict:
    # 라우터가 캐시 키 해석용으로 이미 구한 info 를 넘기면 재사용(중복 git blame 방지).
    if info is None:
        info = git.get_blame_info(repo_path, file_path, line)

    branch = git.get_current_branch(repo_path)
    ticket = extract_ticket(info.message, branch)
    team = get_team_map().get(info.author)

    pr = _safe_find_pr(repo_path, info.commit_hash)
    issues = _safe_find_issues(repo_path, pr)
    followups = git.find_followup_commits(repo_path, ticket, exclude_hash=info.commit_hash)

    explanation = _explain_blame(info, issues)
    source_ref = _format_source_ref(issues)
    primary_issue = issues[0] if issues else None
    attachments = [
        {"label": a.label, "url": a.url}
        for issue in issues
        for a in issue.attachments
    ]

    related = _build_related_changes(issues, pr, followups, file_path)

    return {
        "explanation": explanation,
        "commitHash": info.commit_hash,
        "author": info.author,
        "date": info.date,
        "ticket": ticket,
        "team": team,
        "sourceRef": source_ref,
        "specRef": source_ref,
        "issueUrl": primary_issue.url if primary_issue else None,
        "attachments": attachments,
        "changeStats": {"added": info.added, "removed": info.removed},
        "prInfo": ({"url": pr.url, "lines": pr.added + pr.removed} if pr else None),
        "relatedChanges": related,
        "aiSuggestion": _suggest_improvement(info, issues),
    }


def ask_followup(repo_path: str, file_path: str, line: int, question: str) -> str:
    """현재 라인 블레임 맥락 위에서 들어온 후속 질문에 답한다.

    같은 코드/커밋/이슈 맥락을 다시 모아, 사용자의 질문에 한국어로 답한다.
    """
    info = git.get_blame_info(repo_path, file_path, line)
    pr = _safe_find_pr(repo_path, info.commit_hash)
    issues = _safe_find_issues(repo_path, pr)
    context = _build_context(info, issues)

    instruction = f"""위 변경 맥락에 근거해 사용자의 질문에 한국어로 1~2문장으로 답하세요.
근거가 연관 이슈 본문/첨부에 있으면 핵심 표현을 큰따옴표("…")로 인용하고, 맥락에 없으면 모른다고 솔직히 답하세요.

[사용자 질문]
{question}"""

    try:
        return call_bedrock(
            instruction, system=_SYSTEM_PROMPT, context=context, cache=True, max_tokens=300
        ).strip()
    except Exception:
        return "[Bedrock 미연동] 후속 질문에 답하려면 AWS Bedrock 자격증명이 필요합니다."


def _safe_find_pr(repo_path: str, commit_hash: str) -> PullRequest | None:
    """PR 조회 — 어떤 이유로든 실패하면 None (로컬 결과는 그대로 유지)."""
    try:
        return vcs.find_pr_for_commit(repo_path, commit_hash)
    except Exception:
        return None


def _safe_find_issues(repo_path: str, pr: PullRequest | None) -> list[Issue]:
    """PR 본문에서 연결 이슈를 파싱 — 호스트/토큰/API 어느 단계든 실패 시 빈 리스트."""
    if pr is None or not pr.body:
        return []
    try:
        remote = vcs.detect_remote(repo_path)
        if remote is None:
            return []
        return vcs.find_issues_from_pr_body(remote, pr.body)
    except Exception:
        return []


def _build_related_changes(
    issues: list[Issue], pr: PullRequest | None, followups, current_file: str
) -> list[dict]:
    """'이 변경과 함께 일어난 일' 목록을 조립한다.

    구성: ① 연관 이슈 첨부 문서(doc) ② 같은 PR 의 다른 파일(branch/commit)
          ③ 같은 티켓 후속 커밋(security/commit)
    """
    related: list[dict] = []

    # ① 연관 이슈의 첨부 문서 — 첨부가 없으면 이슈 자체를 한 줄로
    for issue in issues:
        if issue.attachments:
            for att in issue.attachments:
                related.append({
                    "kind": "doc",
                    "title": att.label,
                    "meta": f"Issue #{issue.number}",
                })
                if len(related) >= 3:  # 첨부 카드가 사이드바를 잡아먹지 않게 컷
                    break
        else:
            related.append({
                "kind": "doc",
                "title": f"Issue #{issue.number}: {issue.title}".strip(),
                "meta": "연관 이슈",
            })
        if len(related) >= 3:
            break

    # ② 같은 PR 의 다른 파일들 (현재 파일 제외)
    if pr:
        current_name = os.path.basename(current_file)
        for f in pr.files:
            if os.path.basename(f.path) == current_name:
                continue
            is_new = f.status == "added"
            related.append({
                "kind": "branch" if is_new else "commit",
                "title": f"{os.path.basename(f.path)} {'신규 생성' if is_new else '변경'}",
                "meta": f"+{f.added} 라인 · 같은 PR",
            })
            if len(related) >= 5:  # 사이드바 과밀 방지
                break

    # ③ 같은 티켓을 참조한 후속 커밋
    for c in followups:
        subject = c.get("subject", "")
        is_security = any(term.lower() in subject.lower() for term in _SECURITY_TERMS)
        related.append({
            "kind": "security" if is_security else "commit",
            "title": subject,
            "meta": f"{c.get('date', '')} · {c.get('author', '')}".strip(" ·"),
        })
        if len(related) >= 6:
            break

    return related


def _explain_blame(info: git.BlameInfo, issues: list[Issue]) -> str:
    """코드 + 커밋 메시지 + 연관 이슈를 Bedrock 에 넣어 변경 사유를 추론한다.

    Bedrock 호출이 불가한 환경(자격증명 없음 등)에서는 커밋 메시지를 그대로 반환한다.
    """
    context = _build_context(info, issues)

    instruction = """위 변경 맥락을 종합해, 개발자가 이 코드를 왜 변경했는지 한국어로 1~2문장으로 설명하세요.
기술적 커밋 메시지가 아니라, 연관 이슈와 첨부된 요구사항 문서가 알려주는 '비즈니스상의 진짜 이유'를 우선해 설명하세요.
이슈 본문이나 첨부 라벨에 근거가 있으면 핵심 표현을 큰따옴표("…")로 인용하세요."""

    try:
        return call_bedrock(
            instruction, system=_SYSTEM_PROMPT, context=context, cache=True, max_tokens=300
        ).strip()
    except Exception:
        # Bedrock 미설정/호출 실패 시 git 커밋 메시지로 폴백 (개발/테스트용)
        return f"[Bedrock 미연동] 커밋 메시지: {info.message or '(메시지 없음)'}"


def _suggest_improvement(info: git.BlameInfo, issues: list[Issue]) -> str | None:
    """이 변경 맥락에서 '앞으로 고려하면 좋을 점' 한 문장을 Bedrock 으로 추론한다.

    사이드바 'AI 추론' 섹션용. 단순 코드 리뷰 지적이 아니라, 이슈/커밋 맥락 위에서
    "이 부분은 이후 ~를 함께 보면 좋겠다" 류의 한 문장을 한국어로 뽑는다.

    설명문(_explain_blame)과 달리, Bedrock 호출이 불가하거나 마땅한 제안이 없으면
    None 을 반환한다 — 사이드바는 값이 없으면 'AI 추론' 섹션을 숨기므로,
    유령 텍스트("[Bedrock 미연동]…")를 넣지 않는다.
    """
    context = _build_context(info, issues)

    instruction = """위 변경 맥락을 바탕으로,
앞으로 이 코드를 다룰 때 함께 고려하면 좋을 점을 한국어로 딱 한 문장 제안하세요.
- 단순한 코드 스타일 지적이 아니라, 기획·도메인 맥락에서 의미 있는 한 가지를 짚으세요.
- 맥락이 빈약해 의미 있는 제안이 어렵다면, 다른 말 없이 정확히 "NONE" 만 출력하세요."""

    try:
        suggestion = call_bedrock(
            instruction, system=_SYSTEM_PROMPT, context=context, cache=True, max_tokens=200
        ).strip()
    except Exception:
        return None  # Bedrock 미설정/호출 실패 — 섹션을 숨긴다

    if not suggestion or suggestion.upper().strip(' ."') == "NONE":
        return None
    return suggestion


def _build_context(info: git.BlameInfo, issues: list[Issue]) -> str:
    """설명/AI제안/후속질문 호출이 공유하는 '변경 맥락' 블록.

    이 블록이 프롬프트 캐싱의 캐시 프리픽스가 된다(call_bedrock(context=..., cache=True)).
    analyze_blame 한 번에 _explain_blame + _suggest_improvement 가 같은 context 로 연달아
    호출하므로, 두 번째 호출부터 이 블록이 캐시 적중되어 입력 토큰 비용이 준다.
    핵심: 호출마다 달라지는 '작업 지시문/질문'은 여기 넣지 말고, 변하지 않는 맥락 데이터만 둔다.
    """
    issue_block = _format_issues(issues)
    return f"""[작성자] {info.author}
[날짜] {info.date}
[커밋 메시지]
{info.message}

[변경 내용]
{_truncate_diff(info.diff)}

[연관 이슈]
{issue_block}"""


def _truncate_diff(diff: str) -> str:
    """Bedrock 에 보낼 diff 를 _MAX_DIFF_CHARS 상한으로 자른다(거대 커밋의 토큰 폭발 방지)."""
    if len(diff) <= _MAX_DIFF_CHARS:
        return diff
    return diff[:_MAX_DIFF_CHARS] + "\n…(이하 생략)"


def _format_issues(issues: list[Issue]) -> str:
    if not issues:
        return "(연관 이슈 없음 — 커밋 메시지와 변경 내용만으로 추론하세요.)"
    blocks: list[str] = []
    for issue in issues:
        body = issue.body.strip()
        if len(body) > _MAX_ISSUE_BODY_CHARS:
            body = body[:_MAX_ISSUE_BODY_CHARS] + "…(이하 생략)"
        attachments = (
            "\n  · 첨부: " + ", ".join(a.label for a in issue.attachments)
            if issue.attachments else ""
        )
        blocks.append(f"- Issue #{issue.number} {issue.title}\n{body}{attachments}")
    return "\n\n".join(blocks)


def _format_source_ref(issues: list[Issue]) -> str | None:
    """사이드바 '출처' 칸 표시 — 가장 연관도 높은 이슈를 'Issue #N: 제목' 으로."""
    if not issues:
        return None
    top = issues[0]
    if top.title:
        return f"Issue #{top.number}: {top.title}"
    return f"Issue #{top.number}"


# ─── 다른 기능과 공유되는 텍스트 유틸 ─────────────────────────────────
#
# extract_keywords 는 onboarding(`features/onboarding/backfill.py`) 과
# 요구사항 역추적(`features/traceability/service.py`) 이 import 해서
# 자기네 Bedrock Knowledge Base 조회 쿼리를 만들 때 쓴다.
# blame 본체에서는 더 이상 KB 를 부르지 않지만, 함수 자체는 두 기능이 의존하므로 유지한다.

# KB 검색 정확도를 위해 우대할 결제/정산 도메인 용어 (검색 쿼리 맨 앞에 배치)
_DOMAIN_TERMS = {
    "매입채권", "정산", "환율", "수수료", "결제", "정산서", "지급", "청구",
    "세금계산서", "부가세", "원천징수", "선정산", "후정산", "여신", "한도",
}

# 검색에 무의미한 노이즈 토큰 (conventional-commit 동사 + 일반어)
_STOPWORDS = {
    "feat", "fix", "refactor", "chore", "docs", "test", "perf", "style", "build",
    "ci", "revert", "wip", "update", "add", "remove", "delete", "change", "modify",
    "the", "a", "an", "to", "of", "and", "or", "for", "in", "on", "with",
    "수정", "추가", "변경", "삭제", "반영", "개선", "적용",
}


def extract_keywords(commit_message: str) -> list[str]:
    """커밋 메시지에서 Knowledge Base 조회에 쓸 키워드를 뽑는다.

    전략: conventional-commit prefix 제거 → 한글/영숫자 토큰 추출 → 불용어 제거 →
    결제/정산 도메인 용어를 쿼리 앞으로 끌어올린다(중복 제거, 순서 보존).
    """
    first_line = commit_message.strip().splitlines()[0] if commit_message.strip() else ""
    body = re.sub(r"^\w+(?:\[[^\]]+\])?:\s*", "", first_line)

    tokens = re.findall(r"[가-힣]+|[A-Za-z0-9]{2,}", body)

    seen: set[str] = set()
    domain, rest = [], []
    for tok in tokens:
        low = tok.lower()
        if low in _STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        (domain if tok in _DOMAIN_TERMS else rest).append(tok)

    return domain + rest
