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

import logging
import os
import re

from botocore.exceptions import BotoCoreError, ClientError

from app.core import git, vcs
from app.core.ai_client import call_bedrock
from app.core.commit_classifier import SKIP_TYPES, classify_commit
from app.core.config import get_team_map
from app.core.tickets import extract_ticket
from app.core.vcs import Issue, PullRequest

logger = logging.getLogger(__name__)

# 노이즈 커밋 type → 사용자에게 보일 한국어 라벨
_NOISE_LABELS: dict[str, str] = {
    "docs": "문서",
    "test": "테스트",
    "chore": "설정/잡무",
}

# ask_followup 에서 재사용할 맥락 캐시 — (repo_path, file_path, commit_hash) → context str
# analyze_blame 이 채우고 ask_followup 이 읽는다.  LRU 없이 최대 100건만 보관.
_CONTEXT_CACHE: dict[tuple[str, str, str], str] = {}
_CONTEXT_CACHE_MAX = 100

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
    repo_path: str,
    file_path: str,
    line: int,
    info: git.BlameInfo | None = None,
    branch: str | None = None,
    ticket: str | None = None,
) -> dict:
    # 라우터가 캐시 키 해석용으로 이미 구한 info/branch/ticket 을 넘기면 재사용
    if info is None:
        info = git.get_blame_info(repo_path, file_path, line)
    if branch is None:
        branch = git.get_current_branch(repo_path)
    if ticket is None:
        ticket = extract_ticket(info.message, branch)
    team = get_team_map().get(info.author)

    # 노이즈 커밋(test/chore/docs) 우회 — §6 공통 처리 원칙 #2
    # 분류는 commit_classifier(타임라인과 공유). 우회 시 Bedrock·GitHub API 모두 호출하지 않는다.
    classified = classify_commit({"message": info.message, "subject": info.message.splitlines()[0] if info.message else ""})
    if classified["type"] in SKIP_TYPES:
        return _noise_response(info, ticket, team, classified["type"])

    pr = _safe_find_pr(repo_path, info.commit_hash)
    issues = _safe_find_issues(repo_path, pr, commit_message=info.message)
    followups = git.find_followup_commits(repo_path, ticket, exclude_hash=info.commit_hash)

    context = _build_context(info, issues)
    # ask_followup 이 재사용할 수 있도록 맥락을 캐시
    _cache_key = (repo_path, file_path, info.commit_hash)
    if len(_CONTEXT_CACHE) >= _CONTEXT_CACHE_MAX:
        _CONTEXT_CACHE.pop(next(iter(_CONTEXT_CACHE)))
    _CONTEXT_CACHE[_cache_key] = context

    explanation, ai_degraded = _explain_blame(info, issues, context=context)
    source_ref = _format_source_ref(issues)
    primary_issue = issues[0] if issues else None
    attachments = [
        {"label": a.label, "url": a.url}
        for issue in issues
        for a in issue.attachments
    ]

    related = _build_related_changes(issues, pr, followups, file_path)
    line_history = _build_line_history(repo_path, file_path, line)

    return {
        "explanation": explanation,
        "aiDegraded": ai_degraded,
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
        "lineHistory": line_history,
        "aiSuggestion": _suggest_improvement(info, issues, context=context),
    }


def uncommitted_response(reason: str) -> dict:
    """커밋 이력이 없는 라인(미커밋 파일 등)용 안내 응답.

    git.get_blame_info 가 BlameUnavailable 을 던졌을 때 라우터가 호출한다.
    blamed 커밋이 없으므로 commit/author/date 등은 모두 빈 값이고,
    explanation 에만 사용자에게 보여줄 안내 문구를 담는다.

    reason: 'uncommitted'  — 아직 커밋되지 않은(untracked) 파일
            'no_history'  — 추적은 되지만 해당 라인의 이력을 못 찾음(라인 범위 초과 등)

    TODO(개발자 A): 아래 _UNCOMMITTED_MESSAGE 를 채워 주세요. (5~10줄)
    """
    return {
        "explanation": _UNCOMMITTED_MESSAGE.get(reason, _UNCOMMITTED_MESSAGE["no_history"]),
        "commitHash": "",
        "author": "",
        "date": "",
        "ticket": None,
        "team": None,
        "sourceRef": None,
        "specRef": None,
        "issueUrl": None,
        "attachments": [],
        "changeStats": None,
        "prInfo": None,
        "relatedChanges": [],
        "aiSuggestion": None,
    }


# 👉 사용자 작성 구역 — reason 별 안내 문구
#    'uncommitted': 아직 커밋되지 않아 추적할 이력이 없다는 점 + 다음 행동(커밋하면 분석 가능) 안내
#    'no_history' : 해당 라인의 커밋 이력을 찾지 못했다는 점 안내
_UNCOMMITTED_MESSAGE: dict[str, str] = {
    "uncommitted": (
        "아직 커밋되지 않은 코드라 변경 이력을 추적할 수 없어요. "
        "이 줄을 한 번 커밋하면 그때부터 변경 사유와 연관 이슈를 함께 보여드릴 수 있습니다."
    ),
    "no_history": (
        "이 줄의 커밋 이력을 찾지 못했어요. "
        "파일이 막 추가됐거나 해당 줄이 아직 기록되지 않은 상태일 수 있습니다."
    ),
}


def _noise_response(
    info: git.BlameInfo, ticket: str | None, team: str | None, commit_type: str
) -> dict:
    """노이즈 커밋(test/chore/docs)용 정형 응답.

    Bedrock·GitHub API 호출 없이 BlameResponse 와 동일한 스키마로 응답한다.
    relatedChanges/issueUrl/attachments 등 LLM·외부 의존 필드는 모두 빈 값.
    aiSuggestion 은 None — 사이드바가 해당 섹션을 숨긴다.
    """
    return {
        "explanation": _build_noise_explanation(info, commit_type),
        "commitHash": info.commit_hash,
        "author": info.author,
        "date": info.date,
        "ticket": ticket,
        "team": team,
        "sourceRef": None,
        "specRef": None,
        "issueUrl": None,
        "attachments": [],
        "changeStats": {"added": info.added, "removed": info.removed},
        "prInfo": None,
        "relatedChanges": [],
        "aiSuggestion": None,
    }


def _build_noise_explanation(info: git.BlameInfo, commit_type: str) -> str:
    """노이즈 커밋용 사용자 표시 문구를 조립한다.

    📌 임시 폴백 — 최종 문구는 §10 P1 'TODO: 노이즈 응답 문구 확정' 참조.
    현재는 동작 보장을 위해 보수적인 한 줄을 반환한다.
    """
    label = _NOISE_LABELS.get(commit_type, commit_type)
    first_line = info.message.splitlines()[0] if info.message else ""
    quote = first_line.strip() or "(커밋 메시지 없음)"
    return f'[자동 분류] {label} 정비 커밋입니다 — "{quote}"'


def ask_followup(repo_path: str, file_path: str, line: int, question: str) -> str:
    """현재 라인 블레임 맥락 위에서 들어온 후속 질문에 답한다.

    analyze_blame 이 캐시해 둔 맥락을 재사용해 git/PR/Issue 재조회를 막는다.
    캐시가 없는 경우(첫 질문이 analyze_blame 없이 들어온 경우)에만 재빌드한다.
    """
    info = git.get_blame_info(repo_path, file_path, line)
    context = _CONTEXT_CACHE.get((repo_path, file_path, info.commit_hash))
    if context is None:
        pr = _safe_find_pr(repo_path, info.commit_hash)
        issues = _safe_find_issues(repo_path, pr, commit_message=info.message)
        context = _build_context(info, issues)

    instruction = f"""위 변경 맥락에 근거해 사용자의 질문에 한국어로 1~2문장으로 답하세요.
근거가 연관 이슈 본문/첨부에 있으면 핵심 표현을 큰따옴표("…")로 인용하고, 맥락에 없으면 모른다고 솔직히 답하세요.

[사용자 질문]
{question}"""

    try:
        return call_bedrock(
            instruction, system=_SYSTEM_PROMPT, context=context, cache=True, max_tokens=300
        ).strip()
    except Exception as e:
        logger.exception("Bedrock 후속 질문 응답 실패 — commit=%s", info.commit_hash[:8] if info.commit_hash else "?")
        return _degraded_explanation(info, e)


def _safe_find_pr(repo_path: str, commit_hash: str) -> PullRequest | None:
    """PR 조회 — 어떤 이유로든 실패하면 None (로컬 결과는 그대로 유지)."""
    try:
        return vcs.find_pr_for_commit(repo_path, commit_hash)
    except Exception:
        return None


def _safe_find_issues(
    repo_path: str, pr: PullRequest | None, commit_message: str = ""
) -> list[Issue]:
    """PR 본문에서 연결 이슈를 파싱 — 호스트/토큰/API 어느 단계든 실패 시 빈 리스트.

    PR 본문이 비거나 매칭이 없으면 커밋 메시지의 #N 패턴으로 2차 폴백
    (Squash/Rebase 후 PR 본문이 사라지는 케이스 대비).
    """
    try:
        remote = vcs.detect_remote(repo_path)
        if remote is None:
            return []
        if pr is not None and pr.body:
            issues = vcs.find_issues_from_pr_body(remote, pr.body)
            if issues:
                return issues
        # 폴백: 커밋 메시지의 #N 직접 매칭
        if commit_message:
            return vcs.find_issues_from_commit_message(remote, commit_message)
        return []
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
        pr_files_others = [f for f in pr.files if os.path.basename(f.path) != current_name]
        pr_cap_index = len(related)  # PR 영역 시작점
        for f in pr_files_others:
            is_new = f.status == "added"
            related.append({
                "kind": "branch" if is_new else "commit",
                "title": f"{os.path.basename(f.path)} {'신규 생성' if is_new else '변경'}",
                "meta": f"+{f.added} 라인 · 같은 PR",
            })
            if len(related) >= 5:  # 사이드바 과밀 방지
                break
        # 잘린 PR 파일이 있으면 "외 N건" 한 줄 추가 (이슈 카드/현재 파일 제외 후 잔여)
        shown_pr_files = len(related) - pr_cap_index
        remaining_pr = len(pr_files_others) - shown_pr_files
        if remaining_pr > 0:
            related.append({
                "kind": "commit",
                "title": f"외 {remaining_pr}개 파일",
                "meta": "같은 PR",
            })

    # ③ 같은 티켓을 참조한 후속 커밋
    followup_cap_index = len(related)
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
    shown_followups = len(related) - followup_cap_index
    remaining_followups = len(followups) - shown_followups
    if remaining_followups > 0:
        related.append({
            "kind": "commit",
            "title": f"외 {remaining_followups}개 커밋",
            "meta": "같은 티켓",
        })

    return related


def _build_line_history(repo_path: str, file_path: str, line: int) -> list[dict]:
    """사이드바 '라인 수정 이력' 목록을 조립한다.

    git.get_line_history 로 '이 한 줄이 실제로 바뀐' 커밋들을 받아,
    각 커밋이 참조하는 이슈 수(_count_linked_issues)를 '이슈 N' 배지용으로 덧붙인다.

    git 조회 자체는 가볍지만(한 줄 로그), 커밋별 이슈 수는 GitHub API 를 부르지 않고
    커밋 메시지의 #N 참조만 세어 비용 0 으로 추정한다.
    """
    history = git.get_line_history(repo_path, file_path, line)
    return [
        {
            "hash": c["hash"],
            "author": c["author"],
            "date": c["date"],
            "subject": c["subject"],
            "issueCount": _safe_count_linked_issues(c["subject"]),
        }
        for c in history
    ]


def _safe_count_linked_issues(message: str) -> int:
    """_count_linked_issues 가 아직 미구현(또는 예외)이어도 이력 목록 자체는 살린다.

    배지는 부가 정보이므로, 세는 로직이 없으면 0(배지 숨김)으로 폴백한다.
    """
    try:
        return _count_linked_issues(message)
    except NotImplementedError:
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# 👉 사용자 작성 구역 — '라인 수정 이력' 행의 '이슈 N' 배지 숫자
#
# 한 커밋이 몇 개의 이슈와 엮였는지를, GitHub API 호출 없이 커밋 메시지(제목/본문)
# 텍스트만으로 추정한다. 설계 결정이 들어가는 작은 함수라 직접 채워 주세요(5~10줄).
#
# 고려할 점:
#   · GitHub 이슈 참조 관례는 "#12", "#5" 처럼 '#' + 숫자. 정규식 r"#(\d+)" 로 뽑을 수 있다.
#   · 같은 이슈를 두 번 적은 경우(#12 #12)는 한 번으로 — set() 으로 중복 제거할지?
#   · "PAY-2041" 같은 지라 티켓도 셀지, GitHub 이슈(#N)만 셀지? (스샷 예시는 이슈 기준)
#   · 매칭이 없으면 0 을 반환 — 프론트는 0 이면 배지를 숨긴다.
#
# 반환: 이 커밋이 참조하는 이슈 개수(int)
# ─────────────────────────────────────────────────────────────────────────────
def _count_linked_issues(message: str) -> int:
    # TODO(개발자 A): 위 가이드를 참고해 이슈 참조 개수를 세어 반환하세요.
    raise NotImplementedError("이슈 참조 개수 세는 로직을 구현해 주세요")


def _explain_blame(
    info: git.BlameInfo, issues: list[Issue], *, context: str | None = None
) -> tuple[str, bool]:
    """코드 + 커밋 메시지 + 연관 이슈를 Bedrock 에 넣어 변경 사유를 추론한다.

    반환: (설명, degraded). degraded=True 면 Bedrock 호출에 실패해 폴백 문구를 반환한 것이며,
    호출부(analyze_blame)는 이를 응답의 aiDegraded 로 올려 프론트·DB 캐시가 캐싱을 건너뛰게 한다.

    context 를 미리 받으면 _build_context 재호출을 생략한다(프롬프트 캐시 공유).
    """
    if context is None:
        context = _build_context(info, issues)

    instruction = """위 변경 맥락을 종합해, 개발자가 이 코드를 왜 변경했는지 한국어로 1~2문장으로 설명하세요.
기술적 커밋 메시지가 아니라, 연관 이슈와 첨부된 요구사항 문서가 알려주는 '비즈니스상의 진짜 이유'를 우선해 설명하세요.
이슈 본문이나 첨부 라벨에 근거가 있으면 핵심 표현을 큰따옴표("…")로 인용하세요."""

    try:
        text = call_bedrock(
            instruction, system=_SYSTEM_PROMPT, context=context, cache=True, max_tokens=300
        ).strip()
        return text, False
    except Exception as e:
        # 실제 원인을 로그로 남긴다 — 운영 중 "왜 미연동인지"를 알 수 있게.
        # (이전엔 모든 실패를 동일 문구로 뭉개 진단이 불가능했다.)
        logger.exception("Bedrock 변경 사유 추론 실패 — commit=%s", info.commit_hash[:8] if info.commit_hash else "?")
        return _degraded_explanation(info, e), True


def _degraded_explanation(info: git.BlameInfo, e: Exception) -> str:
    """Bedrock 호출 실패 시 사용자에게 보일 원인별 안내 문구.

    가능하면 실패 원인(세션 토큰 만료/권한/쓰로틀링 등)을 구분해, 다음 행동을 알려준다.
    이 문구는 degraded 응답이라 캐싱되지 않으므로, 원인이 해소되면 다음 분석에서 자동 회복된다.
    """
    code = ""
    if isinstance(e, ClientError):
        code = e.response.get("Error", {}).get("Code", "")

    cause = _BEDROCK_ERROR_HINTS.get(code)
    if cause is None and isinstance(e, BotoCoreError):
        # 자격증명 자체를 못 찾은 경우(NoCredentialsError 등)
        cause = "AWS 자격증명을 찾지 못했어요. backend/.env 의 AWS 키를 확인하고 백엔드를 재시작해 주세요."
    if cause is None:
        cause = "AI 설명 생성에 실패했어요. 백엔드 로그에서 자세한 원인을 확인할 수 있습니다."

    return f"AI 설명을 표시하지 못했습니다 — {cause}"


# Bedrock(boto3) 오류 코드 → 사용자 안내. 코드는 ClientError.response['Error']['Code'].
_BEDROCK_ERROR_HINTS: dict[str, str] = {
    "ExpiredTokenException": "AWS 세션 토큰이 만료됐어요. 자격증명을 갱신하고 백엔드를 재시작해 주세요.",
    "ExpiredToken": "AWS 세션 토큰이 만료됐어요. 자격증명을 갱신하고 백엔드를 재시작해 주세요.",
    "UnrecognizedClientException": "AWS 자격증명이 올바르지 않습니다. 액세스 키/시크릿을 확인해 주세요.",
    "InvalidSignatureException": "AWS 자격증명 서명이 올바르지 않습니다. 키 값을 확인해 주세요.",
    "AccessDeniedException": "이 Bedrock 모델에 대한 접근 권한이 없습니다. 모델 액세스/IAM 권한을 확인해 주세요.",
    "ThrottlingException": "Bedrock 요청이 일시적으로 제한됐어요. 잠시 후 다시 시도해 주세요.",
    "ValidationException": "Bedrock 요청이 거부됐어요(모델 ID/리전 확인 필요).",
    "ResourceNotFoundException": "Bedrock 모델을 찾지 못했어요. BEDROCK_MODEL_ID 와 리전을 확인해 주세요.",
}


def _suggest_improvement(info: git.BlameInfo, issues: list[Issue], *, context: str | None = None) -> str | None:
    """이 변경 맥락에서 '앞으로 고려하면 좋을 점' 한 문장을 Bedrock 으로 추론한다.

    context 를 미리 받으면 _build_context 재호출을 생략한다(프롬프트 캐시 공유).
    설명문(_explain_blame)과 달리, Bedrock 호출이 불가하거나 마땅한 제안이 없으면 None.
    """
    if context is None:
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
    # 빈 메시지/diff (initial commit, merge, 바이너리-only 등)는 LLM 이 무엇이 비었는지
    # 알 수 있도록 명시 라벨로 폴백한다 — 빈 줄만 보내면 환각 가능성↑.
    message = info.message.strip() or "(커밋 메시지 없음)"
    diff = _truncate_diff(info.diff) if info.diff.strip() else "(변경 hunk 없음 — 바이너리/병합 커밋이거나 rename 만 발생)"
    return f"""[작성자] {info.author or "(작성자 미상)"}
[날짜] {info.date or "(날짜 미상)"}
[커밋 메시지]
{message}

[변경 내용]
{diff}

[연관 이슈]
{issue_block}"""


def _truncate_diff(diff: str) -> str:
    """Bedrock 에 보낼 diff 를 _MAX_DIFF_CHARS 상한으로 자른다(거대 커밋의 토큰 폭발 방지).

    전략: **hunk 헤더 우선 보존**.
    - 파일 헤더(`diff --git`/`+++`/`---`)와 hunk 헤더(`@@ … @@`)를 식별해, 가능한 한
      많은 hunk 를 통째로 살린다. 한도가 차면 잘라낸 hunk 들의 헤더만 끝에 모아
      `[잘린 hunks]` 블록으로 붙여 LLM 이 '어느 영역이 잘렸는지'는 인지하게 한다.
    - hunk 헤더가 하나도 없으면(=patch 가 아닌 stat-only 등) head+tail 폴백.
    """
    if len(diff) <= _MAX_DIFF_CHARS:
        return diff

    lines = diff.splitlines()
    # hunk 시작 인덱스 + 파일 헤더 시작 인덱스(파일 단위 컨텍스트 보존용)
    hunk_starts = [i for i, l in enumerate(lines) if l.startswith("@@ ")]
    if not hunk_starts:
        # patch 가 아닌 경우(예: --stat 만) head+tail 폴백
        half = _MAX_DIFF_CHARS // 2
        return diff[:half] + "\n…(중략)…\n" + diff[-half:]

    file_header_idx = next((i for i, l in enumerate(lines) if l.startswith("diff --git")), 0)
    preamble = "\n".join(lines[file_header_idx:hunk_starts[0]])

    # 각 hunk 의 [시작, 다음 hunk 시작) 범위를 본문으로 잡는다
    boundaries = hunk_starts + [len(lines)]
    hunks = [
        "\n".join(lines[boundaries[k]:boundaries[k + 1]])
        for k in range(len(hunk_starts))
    ]

    kept: list[str] = []
    skipped_headers: list[str] = []
    used = len(preamble) + 1
    for hunk in hunks:
        if used + len(hunk) + 1 <= _MAX_DIFF_CHARS:
            kept.append(hunk)
            used += len(hunk) + 1
        else:
            # 본문 대신 헤더 한 줄(@@ … @@)만 남긴다
            header = hunk.splitlines()[0]
            skipped_headers.append(header)

    parts = [preamble] + kept
    if skipped_headers:
        parts.append("[잘린 hunks — 헤더만 보존]\n" + "\n".join(skipped_headers))
    return "\n".join(parts)


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
