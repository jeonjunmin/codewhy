"""이슈 챗봇 비즈니스 로직.

흐름:
  ① 프런트가 보낸 이슈(본문·댓글·활동·연관 커밋)를 텍스트 컨텍스트 블록으로 조립
  ② 연관 커밋의 '실제 코드 변경(diff)' 을 호스팅 API 로 가져와 컨텍스트에 더한다
     — 커밋 메시지(의도)보다 diff(사실)가 "정말 해결됐나?" 류 질문에 정확하기 때문.
  ③ 첨부(이미지/문서)를 내려받아 Converse 멀티모달 블록으로 변환(attachments.build_blocks)
  ④ [컨텍스트 + 멀티모달 + cachePoint] 를 '첫 사용자 메시지' 프리픽스로 고정하고,
     멀티턴 히스토리를 user/assistant 로 이어 붙여 converse_stream 으로 토큰을 흘린다.

컨텍스트가 안정적인 프리픽스라 cachePoint 로 캐싱되어 멀티턴 재전송 비용이 준다.
근거가 컨텍스트에 없으면 '모른다'고 답하도록 프롬프트로 환각을 차단한다(blame 과 동일 철학).

👤 담당: 이슈 챗봇
"""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator

from app.core import vcs
from app.core.ai_client import stream_bedrock
from app.features.issue_chat import attachments
from app.features.issue_chat.schemas import IssueChatRequest

logger = logging.getLogger(__name__)

# 컨텍스트 토큰 폭주 방지 상한(문자 수).
_MAX_BODY = 6000
_MAX_COMMENT = 1500
_MAX_COMMENTS = 40
# 연관 커밋 diff — 토큰·지연 방어용 상한.
_MAX_DIFF_COMMITS = 5             # diff 를 가져올 커밋 수(최신 우선)
_MAX_DIFF_CHARS = 4000            # 커밋 1건 diff 의 문자 수 상한

_SYSTEM_PROMPT = (
    "당신은 특정 이슈 하나의 맥락만 학습한 분석 보조원입니다. "
    "주어진 이슈 컨텍스트(제목·본문·라벨·담당자·활동/댓글·첨부 문서와 이미지·연관 커밋과 그 코드 변경 diff)"
    "에만 근거해 한국어로 답하세요. "
    "답변은 '무엇이 요구되었고, 왜 그랬으며, 어떻게 처리·결정되었는가' 같은 내용·의도·맥락의 분석에 집중하세요. "
    "'정말 해결됐는가' 같은 검증성 질문에는 커밋 메시지가 아니라 '[연관 커밋 코드 변경(diff)]' 의 실제 코드 변경을 "
    "근거로 판단하세요(메시지는 의도일 뿐, diff 가 사실입니다). diff 가 없으면 코드 수준 검증은 불가하다고 밝히세요. "
    "커밋 해시나 주소 같은 식별자는 답변의 중심이 아닙니다 — 그 자체를 나열하지 말고, "
    "정말 필요할 때만 문장 끝에 짧게 곁들이세요. "
    "근거가 첨부 문서나 댓글에 있으면 출처(파일명·작성자)를 자연스럽게 언급하되, 인용보다 해석을 우선하세요. "
    "핵심을 먼저 말하고 필요하면 짧은 목록으로 정리해 읽기 쉽게 쓰세요. "
    "컨텍스트에 없는 내용은 추측하지 말고 '이 이슈의 정보만으로는 알 수 없다'고 솔직히 답하세요."
)


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + " …(생략)"


def _collect_linked_commits(req: IssueChatRequest) -> list[dict]:
    """활동 타임라인의 커밋 이벤트 + 프런트가 보낸 linkedCommits 를 sha 기준 중복 제거."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(sha: str, summary: str):
        sha = (sha or "").strip()
        if not sha or sha in seen:
            return
        seen.add(sha)
        out.append({"sha": sha, "summary": (summary or "").strip()})

    for c in req.comments or []:
        if isinstance(c, dict) and c.get("event") in ("committed", "referenced"):
            add(c.get("commitSha") or "", c.get("commitSummary") or "")
    for lc in req.linkedCommits or []:
        add(lc.sha, lc.summary)
    return out


def fetch_commit_diffs(req: IssueChatRequest, linked: list[dict]) -> dict[str, str]:
    """연관 커밋의 실제 코드 변경(diff)을 호스팅 API 로 병렬 조회한다(블로킹 — 스레드에서 호출).

    remote 미파싱(remoteUrl 없음)·토큰 미설정·API 실패 시 빈 dict — 챗봇은 메시지만으로 진행한다.
    """
    remote = vcs.parse_remote(req.remoteUrl)
    if not remote or not linked:
        return {}
    targets = linked[:_MAX_DIFF_COMMITS]

    def one(lc: dict) -> tuple[str, str]:
        return lc["sha"], vcs.fetch_commit_diff(remote, lc["sha"], max_chars=_MAX_DIFF_CHARS)

    out: dict[str, str] = {}
    try:
        with ThreadPoolExecutor(max_workers=min(5, len(targets))) as ex:
            for sha, diff in ex.map(one, targets):
                if diff:
                    out[sha] = diff
    except Exception as exc:  # 어떤 이유로든 실패해도 대화는 계속
        logger.warning("[issue_chat] 커밋 diff 조회 실패 — 메시지만으로 진행: %s", exc)
    return out


def _build_issue_context(
    req: IssueChatRequest, skipped_attachments: list[str], diffs: dict[str, str] | None = None
) -> str:
    """이슈 전체를 한 덩어리 텍스트 컨텍스트로 조립한다."""
    diffs = diffs or {}
    lines: list[str] = ["[이슈]"]
    head = f"#{req.issueNumber} " if req.issueNumber is not None else ""
    head += req.title or "(제목 없음)"
    if req.state:
        head += f"  (상태: {req.state})"
    lines.append(head)
    if req.labels:
        lines.append("라벨: " + ", ".join(req.labels))
    if req.assignee:
        lines.append("담당자: " + req.assignee)

    lines.append("\n[본문]")
    lines.append(_truncate(req.body, _MAX_BODY) or "(본문 없음)")

    atts = attachments.collect_attachments(req)
    if atts:
        lines.append("\n[첨부]")
        skip = set(skipped_attachments)
        for a in atts:
            label = a["label"] or a["url"]
            note = "  ※ 내용 미첨부(형식/용량/접근 제한) — 링크만" if label in skip else ""
            lines.append(f"- {label} ({a['url']}){note}")

    comments = req.comments or []
    if comments:
        lines.append("\n[활동/댓글]")
        for c in comments[:_MAX_COMMENTS]:
            if not isinstance(c, dict):
                continue
            when = (c.get("createdAt") or "")[:10]
            who = c.get("author") or "?"
            if c.get("kind") == "comment":
                lines.append(f"({when}) {who}: {_truncate(c.get('body') or '', _MAX_COMMENT)}")
            else:
                ev = c.get("event") or "event"
                if ev in ("committed", "referenced"):
                    sha = (c.get("commitSha") or "")[:7]
                    lines.append(f"({when}) [{ev}] {sha} {c.get('commitSummary') or ''}")
                elif ev == "labeled":
                    lines.append(f"({when}) [labeled] {who} → {c.get('label') or ''}")
                elif ev == "assigned":
                    lines.append(f"({when}) [assigned] → {c.get('assignee') or ''}")
                else:
                    lines.append(f"({when}) [{ev}] {who} {_truncate(c.get('body') or '', 200)}")

    linked = _collect_linked_commits(req)
    if linked:
        lines.append("\n[연관 커밋]")
        for lc in linked:
            lines.append(f"- {lc['sha'][:7]} {lc['summary']}")

    # 실제 코드 변경(diff) — 커밋 메시지보다 정확한 검증 근거.
    if diffs:
        lines.append("\n[연관 커밋 코드 변경(diff)]")
        for lc in linked:
            d = diffs.get(lc["sha"])
            if d:
                lines.append(f"### {lc['sha'][:7]} {lc['summary']}")
                lines.append(d)

    return "\n".join(lines)


def _build_messages(req: IssueChatRequest, context: str, mm_blocks: list[dict]) -> list[dict]:
    """멀티턴 converse 메시지를 만든다 — 첫 사용자 턴에 컨텍스트(+첨부 블록)를 고정.

    Converse 는 첫 메시지가 user 이고 user/assistant 가 번갈아야 한다(프런트 채팅이 이를 보장).

    cachePoint 주의: document/image 블록과 같은 content 배열에 cachePoint 를 함께 넣으면
    일부 모델에서 ConverseStream 이 ValidationException(`content.N.type: Field required`)을 낸다.
    캐싱은 비용 최적화일 뿐이므로, 멀티모달 블록이 없을 때(텍스트 전용)만 캐시 분기점을 둔다.
    """
    messages: list[dict] = []
    for i, m in enumerate(req.messages):
        role = "assistant" if m.role == "assistant" else "user"
        if i == 0:
            content: list[dict] = [{"text": context}, *mm_blocks]
            if not mm_blocks:
                content.append({"cachePoint": {"type": "default"}})  # 텍스트 전용일 때만 안정 프리픽스 캐싱
            content.append({"text": m.content})
        else:
            content = [{"text": m.content}]
        messages.append({"role": role, "content": content})
    return messages


async def stream_chat(req: IssueChatRequest) -> AsyncGenerator[str, None]:
    """이슈 컨텍스트+첨부 위에서 멀티턴 답변을 SSE(`data: ...\\n\\n`) 프레임으로 흘린다."""
    if not req.messages:
        yield f"data: {json.dumps({'error': '질문이 비어 있습니다.'}, ensure_ascii=False)}\n\n"
        return

    # 첨부 변환 + 연관 커밋 diff 조회는 둘 다 블로킹 I/O — 스레드로 동시에 돌려 지연을 줄인다.
    linked = _collect_linked_commits(req)

    async def _attachments():
        try:
            return await asyncio.to_thread(attachments.build_blocks, req)
        except Exception as exc:  # 첨부 전체 실패해도 대화는 진행
            logger.warning("[issue_chat] 첨부 변환 실패 — 텍스트만으로 진행: %s", exc)
            return [], []

    async def _diffs():
        return await asyncio.to_thread(fetch_commit_diffs, req, linked)

    (mm_blocks, skipped), diffs = await asyncio.gather(_attachments(), _diffs())

    context = _build_issue_context(req, skipped, diffs)
    messages = _build_messages(req, context, mm_blocks)
    logger.info("[issue_chat] 🔴 스트리밍 시작 — issue=#%s  컨텍스트 %d자  멀티모달 %d블록  diff %d건  턴 %d",
                req.issueNumber, len(context), len(mm_blocks), len(diffs), len(req.messages))

    try:
        async for delta in stream_bedrock(messages, system=_SYSTEM_PROMPT, max_tokens=1024):
            yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
    except Exception as exc:
        logger.exception("[issue_chat] 스트리밍 중 오류 — issue=#%s", req.issueNumber)
        yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
        return

    yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
