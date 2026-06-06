"""Context Blame 비즈니스 로직.

git 으로 라인 단위 마지막 커밋(diff + 커밋 메시지)을 가져오고,
커밋 메시지 키워드로 Bedrock Knowledge Base 에서 연관 기획서 단락을 조회한 뒤,
코드 + 커밋 메시지 + 기획서 단락을 Bedrock 에 한꺼번에 넣어 "진짜 변경 이유"를 추론한다.
여기에 호스팅(PR) 맥락과 후속 커밋을 더해 사이드바 디자인의 모든 필드를 채운다.

RAG 흐름:
  1. git.get_blame_info  — diff + 커밋 메시지 + 변경 라인 수 추출
  2. extract_keywords    — 커밋 메시지에서 KB 조회용 키워드 추출
  3. knowledge_base      — 키워드로 기획서 단락 retrieve
  4. _explain_blame      — 코드 + 커밋 + 기획서 단락을 Bedrock Converse 로 정렬
  5. vcs / followups     — PR 단위 변경 + 같은 티켓 후속 커밋으로 '함께 일어난 일' 조립

👤 담당: 개발자 A
"""

import os
import re

from app.core import git, knowledge_base, vcs
from app.core.ai_client import call_bedrock
from app.core.config import get_team_map
from app.core.knowledge_base import Passage
from app.core.tickets import extract_ticket

_SYSTEM_PROMPT = (
    "당신은 코드 변경의 '기획 의도'를 설명하는 도우미입니다. "
    "git 커밋 메시지의 기술적 표현 뒤에 숨은, 기획서상의 진짜 이유를 추론해 "
    "비개발자도 이해할 수 있는 한국어로 설명하세요."
)

# Bedrock 에 보낼 diff 의 문자 수 상한 — 거대 커밋의 토큰 폭발(비용·지연)을 막는다.
_MAX_DIFF_CHARS = 2000

# 후속 변경을 'security' 로 분류할 도메인 신호어
_SECURITY_TERMS = ("KYC", "감사", "audit", "보안", "security", "권한", "auth")

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


def analyze_blame(
    repo_path: str, file_path: str, line: int, info: git.BlameInfo | None = None
) -> dict:
    # 라우터가 캐시 키 해석용으로 이미 구한 info 를 넘기면 재사용(중복 git blame 방지).
    if info is None:
        info = git.get_blame_info(repo_path, file_path, line)
    keywords = extract_keywords(info.message)
    passages = knowledge_base.retrieve_passages(" ".join(keywords))

    explanation = _explain_blame(info, passages)
    source_ref = _format_source_ref(passages)

    branch = git.get_current_branch(repo_path)
    ticket = extract_ticket(info.message, branch)
    team = get_team_map().get(info.author)

    pr = _safe_find_pr(repo_path, info.commit_hash)
    followups = git.find_followup_commits(repo_path, ticket, exclude_hash=info.commit_hash)

    related = _build_related_changes(passages, pr, followups, file_path)

    return {
        "explanation": explanation,
        "commitHash": info.commit_hash,
        "author": info.author,
        "date": info.date,
        "ticket": ticket,
        "team": team,
        "sourceRef": source_ref,
        "specRef": source_ref,
        "changeStats": {"added": info.added, "removed": info.removed},
        "prInfo": ({"url": pr.url, "lines": pr.added + pr.removed} if pr else None),
        "relatedChanges": related,
        "aiSuggestion": _suggest_improvement(info, passages),
    }


def ask_followup(repo_path: str, file_path: str, line: int, question: str) -> str:
    """현재 라인 블레임 맥락 위에서 들어온 후속 질문에 답한다.

    같은 코드/커밋/기획서 단락을 컨텍스트로 다시 모아, 사용자의 질문에 한국어로 답한다.
    """
    info = git.get_blame_info(repo_path, file_path, line)
    passages = knowledge_base.retrieve_passages(" ".join(extract_keywords(info.message)))
    context = _build_context(info, passages)

    instruction = f"""위 변경 맥락에 근거해 사용자의 질문에 한국어로 1~2문장으로 답하세요.
근거가 기획서 단락에 있으면 핵심 표현을 큰따옴표("…")로 인용하고, 맥락에 없으면 모른다고 솔직히 답하세요.

[사용자 질문]
{question}"""

    try:
        return call_bedrock(
            instruction, system=_SYSTEM_PROMPT, context=context, cache=True, max_tokens=300
        ).strip()
    except Exception:
        return "[Bedrock 미연동] 후속 질문에 답하려면 AWS Bedrock 자격증명이 필요합니다."


def extract_keywords(commit_message: str) -> list[str]:
    """커밋 메시지에서 Knowledge Base 조회에 쓸 키워드를 뽑는다.

    이 키워드가 RAG 검색 품질을 좌우한다 — 어떤 단어로 KB 를 조회하느냐에 따라
    가져오는 기획서 단락이 달라지기 때문이다.

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


def _safe_find_pr(repo_path: str, commit_hash: str):
    """PR 조회 — 어떤 이유로든 실패하면 None (로컬 결과는 그대로 유지)."""
    try:
        return vcs.find_pr_for_commit(repo_path, commit_hash)
    except Exception:
        return None


def _build_related_changes(passages, pr, followups, current_file: str) -> list[dict]:
    """'이 변경과 함께 일어난 일' 목록을 조립한다.

    구성: ① 연관 기획서 단락(doc) ② 같은 PR 의 다른 파일(branch/commit)
          ③ 같은 티켓 후속 커밋(security/commit)
    """
    related: list[dict] = []

    # ① 기획서 단락
    if passages:
        top = passages[0]
        section = f" §{top.section}" if top.section else ""
        related.append({
            "kind": "doc",
            "title": f"{top.source}{section} 단락",
            "meta": "연관 기획서",
        })

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


def _explain_blame(info: git.BlameInfo, passages: list[Passage]) -> str:
    """코드 + 커밋 메시지 + 기획서 단락을 Bedrock 에 넣어 변경 사유를 추론한다.

    Bedrock 호출이 불가한 환경(자격증명 없음 등)에서는 커밋 메시지를 그대로 반환한다.
    """
    context = _build_context(info, passages)

    instruction = """위 변경 맥락을 종합해, 개발자가 이 코드를 왜 변경했는지 한국어로 1~2문장으로 설명하세요.
기술적 커밋 메시지가 아니라, 기획서가 알려주는 '비즈니스상의 진짜 이유'를 우선해 설명하세요.
기획서 단락에 근거가 있으면 핵심 표현을 큰따옴표("…")로 인용하세요."""

    try:
        return call_bedrock(
            instruction, system=_SYSTEM_PROMPT, context=context, cache=True, max_tokens=300
        ).strip()
    except Exception:
        # Bedrock 미설정/호출 실패 시 git 커밋 메시지로 폴백 (개발/테스트용)
        return f"[Bedrock 미연동] 커밋 메시지: {info.message or '(메시지 없음)'}"


def _suggest_improvement(info: git.BlameInfo, passages: list[Passage]) -> str | None:
    """이 변경 맥락에서 '앞으로 고려하면 좋을 점' 한 문장을 Bedrock 으로 추론한다.

    사이드바 'AI 추론' 섹션용. 단순 코드 리뷰 지적이 아니라, 기획서/커밋 맥락 위에서
    "이 부분은 이후 ~를 함께 보면 좋겠다" 류의 한 문장을 한국어로 뽑는다.

    설명문(_explain_blame)과 달리, Bedrock 호출이 불가하거나 마땅한 제안이 없으면
    None 을 반환한다 — 사이드바는 값이 없으면 'AI 추론' 섹션을 숨기므로,
    유령 텍스트("[Bedrock 미연동]…")를 넣지 않는다.
    """
    context = _build_context(info, passages)

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


def _build_context(info: git.BlameInfo, passages: list[Passage]) -> str:
    """설명/AI제안/후속질문 호출이 공유하는 '변경 맥락' 블록.

    이 블록이 프롬프트 캐싱의 캐시 프리픽스가 된다(call_bedrock(context=..., cache=True)).
    analyze_blame 한 번에 _explain_blame + _suggest_improvement 가 같은 context 로 연달아
    호출하므로, 두 번째 호출부터 이 블록이 캐시 적중되어 입력 토큰 비용이 준다.
    핵심: 호출마다 달라지는 '작업 지시문/질문'은 여기 넣지 말고, 변하지 않는 맥락 데이터만 둔다.

    👤 사용자 기여 포인트: 어떤 필드를 맥락에 넣고(작성자/날짜/메시지/diff/기획서 단락) 무엇을
       지시문으로 뺄지의 경계가 캐시 적중률과 설명 품질을 좌우한다. 아래는 동작하는 기본 구성.
    """
    spec_block = _format_passages(passages)
    return f"""[작성자] {info.author}
[날짜] {info.date}
[커밋 메시지]
{info.message}

[변경 내용]
{_truncate_diff(info.diff)}

[연관 기획서 단락]
{spec_block}"""


def _truncate_diff(diff: str) -> str:
    """Bedrock 에 보낼 diff 를 _MAX_DIFF_CHARS 상한으로 자른다(거대 커밋의 토큰 폭발 방지).

    👤 사용자 기여 포인트: 자르는 전략은 설명 품질 vs 토큰 비용의 트레이드오프다.
       - head cap(앞 N자): 가장 단순, 뒷부분 변경 손실  ← 현재 기본값
       - head + tail(앞뒤 절반씩): 변경의 시작/끝 맥락 보존
       - 파일 경로/`@@` hunk 헤더 우선 보존: 구조 신호 유지
       본인 전략으로 아래 한 줄을 교체하세요.
    """
    if len(diff) <= _MAX_DIFF_CHARS:
        return diff
    return diff[:_MAX_DIFF_CHARS] + "\n…(이하 생략)"


def _format_passages(passages: list[Passage]) -> str:
    if not passages:
        return "(연관 기획서 단락 없음 — 커밋 메시지와 변경 내용만으로 추론하세요.)"
    return "\n\n".join(f"- ({p.source}) {p.text}" for p in passages)


def _format_source_ref(passages: list[Passage]) -> str | None:
    """사이드바 '출처' 칸에 표시할, 가장 연관도 높은 기획서 출처(§섹션 포함)."""
    return passages[0].source_ref() if passages else None
