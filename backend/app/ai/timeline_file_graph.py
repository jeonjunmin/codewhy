"""파일별 타임라인 분석 — LangGraph 에이전트.

그래프 흐름:
    analyze ─→ parse ─→ validate ─(valid)──────────────→ END
                           │
                           └─(invalid, retry < MAX)──→ analyze  ← 순환

• analyze  : Bedrock LLM astream 호출 → raw_response 누적
             (내부 토큰이 LangGraph 콜백 시스템을 통해 on_chat_model_stream 이벤트로 전파)
• parse    : raw_response JSON 파싱 → summary + milestones 추출
• validate : milestones JSONB 포맷 검증 — 실패 시 retry_count 증가 후 analyze 로 순환
             최대 재시도 횟수 초과 시 현재 결과 그대로 END

👤 담당: 개발자 B
"""

import json
import logging
import re
from typing import Any, AsyncGenerator, TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

from app.core.bedrock import get_bedrock_llm

logger = logging.getLogger("uvicorn.error")

_GRAPH_NAME = "timeline_summary_graph"
_MAX_RETRIES = 2

_TYPE_PERSPECTIVE: dict[str, str] = {
    "feat":     "기능 추가의 역사",
    "fix":      "디버깅 및 안정화의 역사",
    "refactor": "구조 개선의 역사",
    "perf":     "성능 최적화의 역사",
    "docs":     "문서화의 역사",
    "style":    "코드 스타일 정리의 역사",
}


# ── State ──────────────────────────────────────────────────────────────────────

class TimelineState(TypedDict):
    """노드 간 공유되는 전역 상태 객체."""
    file_path: str
    groups_text: str       # 결정론적 묶음 포맷 (service 가 group_commits_into_milestones 로 생성)
    num_groups: int        # 묶음 수 — LLM 에게 milestones 개수를 명시할 때 사용
    commit_type: str
    commit_domain: str
    raw_response: str      # LLM 누적 출력 (analyze 노드가 채움)
    summary: str           # parse 노드가 추출
    milestones: list       # parse 노드가 추출
    retry_count: int       # validate 실패 시 증가
    validation_ok: bool    # validate 노드가 설정


# ── 프롬프트 빌더 ───────────────────────────────────────────────────────────────

def _build_prompt(
    file_path: str,
    groups_text: str,
    num_groups: int,
    commit_type: str,
    commit_domain: str,
) -> str:
    perspective = _TYPE_PERSPECTIVE.get(commit_type, f"'{commit_type}' 작업의 역사")
    domain_hint = f"[{commit_domain}] 도메인 " if commit_domain else ""

    return f"""아래는 {domain_hint}소스 파일 `{file_path}` 의 Git 커밋 이력을,
관련된 변경끼리 '묶음'으로 미리 그룹핑한 것입니다(같은 이슈/같은 작업/같은 시기 기준).
이 파일은 {commit_domain or '알 수 없는'} 도메인의 '{commit_type}' 작업이야.
**{perspective}** 관점으로 변경 흐름을 분석하세요.

반드시 아래 JSON 형식으로만 응답하세요. 코드블록, 마크다운, 추가 설명 없이 JSON 만 반환하세요:
{{
  "summary": "이 파일의 변경 흐름 요약 (한국어 3~5문장, 기획/비즈니스 의도 중심)",
  "milestones": [
    {{"date": "YYYY-MM-DD", "description": "그 묶음이 이룬 변화 한 줄"}}
  ]
}}

규칙:
- milestones 는 아래 '묶음'과 1:1로, 같은 순서로 정확히 {num_groups}개 만드세요.
- 각 description 은 그 묶음의 커밋들을 아우르는 '무엇을 왜 했는가'를 한 줄로 쓰세요
  (개별 커밋 나열·기술 용어가 아니라, 묶음 전체의 기획·비즈니스 의도 중심).
- date 는 각 묶음에 표기된 마지막 날짜를 그대로 쓰세요. 임의의 날짜를 만들지 마세요.

[묶음별 커밋 이력]
{groups_text}"""


# ── 노드 ───────────────────────────────────────────────────────────────────────

async def _analyze_node(state: TimelineState) -> dict:
    """Bedrock LLM 을 astream 으로 호출 — 토큰을 누적해 raw_response 를 갱신한다.

    llm.astream() 내부 청크가 LangGraph 콜백 시스템을 통해
    on_chat_model_stream 이벤트로 상위 astream_events 호출자에게 실시간 전파된다.
    """
    logger.info(
        "[LangGraph] 'analyze' 진입 — file=%s  retry=%d",
        state["file_path"], state["retry_count"],
    )
    llm = get_bedrock_llm(max_tokens=800)
    prompt = _build_prompt(
        file_path=state["file_path"],
        groups_text=state["groups_text"],
        num_groups=state["num_groups"],
        commit_type=state["commit_type"],
        commit_domain=state["commit_domain"],
    )
    full_text = ""
    async for chunk in llm.astream([HumanMessage(content=prompt)]):
        if isinstance(chunk.content, str):
            full_text += chunk.content
    logger.info("[LangGraph] 'analyze' 완료 — raw_response=%d자", len(full_text))
    return {"raw_response": full_text}


async def _parse_node(state: TimelineState) -> dict:
    """raw_response JSON 을 파싱해 summary + milestones 를 state 에 쓴다."""
    logger.info("[LangGraph] 'parse' 진입")
    result = parse_ai_response(state["raw_response"])
    logger.info(
        "[LangGraph] 'parse' 완료 — summary=%d자  milestones=%d건",
        len(result.get("summary", "")), len(result.get("milestones", [])),
    )
    return result


async def _validate_node(state: TimelineState) -> dict:
    """milestones JSONB 포맷을 검증한다.

    통과: validation_ok=True → 조건부 에지가 END 로 라우팅
    실패: retry_count 증가 → MAX_RETRIES 미만이면 analyze 로 순환 재시도
    """
    logger.info("[LangGraph] 'validate' 진입 — milestones=%d건", len(state["milestones"]))
    milestones = state["milestones"]
    valid = (
        isinstance(milestones, list)
        and len(milestones) > 0
        and all(
            isinstance(m, dict)
            and isinstance(m.get("date"), str)
            and isinstance(m.get("description"), str)
            for m in milestones
        )
    )
    new_retry = state["retry_count"] + (0 if valid else 1)
    logger.info("[LangGraph] 'validate' 완료 — valid=%s  retry_count=%d", valid, new_retry)
    return {"validation_ok": valid, "retry_count": new_retry}


def _route_after_validate(state: TimelineState) -> str:
    """조건부 에지 라우터 — 검증 결과·재시도 한도로 다음 노드를 결정."""
    if state["validation_ok"]:
        logger.info("🔀 [LangGraph Edge] validate → END  (검증 통과)")
        return END
    if state["retry_count"] < _MAX_RETRIES:
        logger.warning(
            "🔀 [LangGraph Edge] validate → analyze  (검증 실패, 재시도 %d/%d)",
            state["retry_count"], _MAX_RETRIES,
        )
        return "analyze"
    logger.warning(
        "🔀 [LangGraph Edge] validate → END  (최대 재시도 %d 회 초과, 현재 결과로 종료)",
        _MAX_RETRIES,
    )
    return END


# ── 그래프 조립 ─────────────────────────────────────────────────────────────────

_workflow = StateGraph(TimelineState)
_workflow.add_node("analyze",  _analyze_node)
_workflow.add_node("parse",    _parse_node)
_workflow.add_node("validate", _validate_node)

_workflow.set_entry_point("analyze")
_workflow.add_edge("analyze", "parse")
_workflow.add_edge("parse",   "validate")
_workflow.add_conditional_edges(
    "validate",
    _route_after_validate,
    {END: END, "analyze": "analyze"},
)

_GRAPH = _workflow.compile()
_GRAPH.name = _GRAPH_NAME  # astream_events on_chain_end 필터링에 사용


# ── 스트리밍 JSON 파서 ──────────────────────────────────────────────────────────

class _SummaryExtractor:
    """LLM 스트리밍 토큰에서 'summary' 필드 값만 추출하는 상태 머신.

    LLM 이 출력하는 전체 JSON 스트림 중 ``"summary": "..."`` 안의 텍스트만 반환하고
    JSON 구조 문자(키·괄호·milestones 배열 등)는 모두 버린다.

    feed() 호출마다 즉시 반환할 수 있는 순수 텍스트를 반환한다.
    done 이 True 가 된 이후에는 항상 "" 를 반환한다.
    """

    _MARKER  = '"summary"'
    _ESC_MAP = {'"': '"', '\\': '\\', '/': '/', 'n': '\n', 'r': '\r',
                't': '\t',  'b': '\b',  'f': '\f'}

    def __init__(self) -> None:
        self._buf        = ""
        self._in_summary = False  # "summary": " 이후부터 True
        self._done       = False  # 닫는 " 발견 후 True

    @property
    def done(self) -> bool:
        return self._done

    def feed(self, chunk: str) -> str:
        """토큰 청크를 받아 순수 summary 텍스트만 반환한다."""
        if self._done:
            return ""
        self._buf += chunk
        if not self._in_summary:
            self._try_enter_summary()
            if not self._in_summary:
                return ""
        return self._extract()

    def _try_enter_summary(self) -> None:
        """버퍼에서 `"summary":` 패턴과 여는 따옴표를 찾아 _in_summary 를 설정한다."""
        idx = self._buf.find(self._MARKER)
        if idx == -1:
            # 마커 일부가 버퍼 끝에 걸쳐 있을 수 있으므로 마커 길이-1 만큼 유지
            keep = len(self._MARKER) - 1
            if len(self._buf) > keep:
                self._buf = self._buf[-keep:]
            return

        after_marker = self._buf[idx + len(self._MARKER):]
        colon_idx = after_marker.find(':')
        if colon_idx == -1:
            self._buf = self._buf[idx:]   # ':' 아직 미도착 — 마커부터 유지
            return

        after_colon  = after_marker[colon_idx + 1:]
        stripped     = after_colon.lstrip()
        if not stripped:
            self._buf = self._buf[idx:]   # '"' 아직 미도착 — 대기
            return
        if stripped[0] != '"':
            self._done = True             # 예상치 못한 형식 — 포기
            return

        # 여는 '"' 의 절대 위치를 계산해 그 다음부터 버퍼 재설정
        leading_spaces = len(after_colon) - len(stripped)
        open_quote_abs = idx + len(self._MARKER) + colon_idx + 1 + leading_spaces
        self._buf       = self._buf[open_quote_abs + 1:]  # '"' 바로 다음부터
        self._in_summary = True

    def _extract(self) -> str:
        """버퍼에서 JSON 이스케이프를 처리하며 닫는 '"' 직전까지의 텍스트를 추출한다."""
        result: list[str] = []
        buf = self._buf
        i   = 0

        while i < len(buf):
            ch = buf[i]

            if ch == '\\':
                if i + 1 >= len(buf):
                    break                  # 이스케이프 다음 문자 미도착 — 대기
                nxt = buf[i + 1]
                if nxt in self._ESC_MAP:
                    result.append(self._ESC_MAP[nxt])
                    i += 2
                elif nxt == 'u':
                    if i + 5 >= len(buf):
                        break              # \uXXXX 4자리 미도착 — 대기
                    try:
                        result.append(chr(int(buf[i + 2:i + 6], 16)))
                        i += 6
                    except ValueError:
                        i += 1
                else:
                    i += 1                 # 알 수 없는 이스케이프 — 건너뜀

            elif ch == '"':
                # 닫는 따옴표 — summary 추출 완료
                self._done = True
                self._buf  = buf[i + 1:]
                return ''.join(result)

            else:
                result.append(ch)
                i += 1

        self._buf = buf[i:]
        return ''.join(result)


# ── 퍼블릭 API ─────────────────────────────────────────────────────────────────

def parse_ai_response(raw: str) -> dict[str, Any]:
    """Bedrock 누적 응답에서 summary + milestones 를 추출한다.

    JSON 파싱 성공 시 정제된 dict, 실패 시 raw 텍스트를 summary 로 폴백.
    _parse_node 와 service 계층 폴백 경로에서 동일하게 사용한다.
    """
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
    try:
        data = json.loads(cleaned)
        summary = str(data.get("summary", "")).strip() or raw
        milestones = data.get("milestones", [])
        if isinstance(milestones, list):
            milestones = [
                {"date": str(m.get("date", "")), "description": str(m.get("description", ""))}
                for m in milestones
                if isinstance(m, dict)
            ]
        else:
            milestones = []
        return {"summary": summary, "milestones": milestones}
    except Exception:
        return {"summary": raw, "milestones": []}


async def stream_file_summary(
    file_path: str,
    groups_text: str,
    num_groups: int,
    commit_type: str,
    commit_domain: str,
) -> AsyncGenerator[str | dict, None]:
    """그래프를 astream_events 로 실행해 두 종류의 아이템을 yield 한다.

    str  → LLM 토큰 델타      (service 가 SSE data:{delta} 프레임으로 래핑)
    dict → 최종 파싱 결과     (service 가 SSE data:{done} 프레임 + DB 저장에 사용)
           {"summary": ..., "milestones": [...]}

    validate 노드가 실패해 analyze 로 순환할 경우, 재시도 LLM 호출의 토큰도
    실시간으로 yield 된다 — 프론트엔드에서는 자연스럽게 이어쓰기로 보인다.
    """
    initial: TimelineState = {
        "file_path":    file_path,
        "groups_text":  groups_text,
        "num_groups":   num_groups,
        "commit_type":  commit_type,
        "commit_domain": commit_domain,
        "raw_response": "",
        "summary":      "",
        "milestones":   [],
        "retry_count":  0,
        "validation_ok": False,
    }

    extractor = _SummaryExtractor()

    async for event in _GRAPH.astream_events(initial, version="v2"):
        ev = event["event"]

        if ev == "on_chat_model_stream" and not extractor.done:
            # analyze 노드 내 llm.astream() 의 토큰 청크
            # _SummaryExtractor 로 필터링 — "summary" 필드 값만 프론트엔드로 전달
            chunk = event["data"]["chunk"]
            piece = chunk.content if isinstance(chunk.content, str) else ""
            if piece:
                text = extractor.feed(piece)
                if text:
                    yield text  # str — 순수 summary 텍스트만, JSON 구조 문자 제거됨

        elif ev == "on_chain_end" and event.get("name") == _GRAPH_NAME:
            # 그래프 전체 실행 완료 — 최종 state 를 꺼내 파싱 결과를 전달
            output = event["data"].get("output", {})
            if isinstance(output, dict):
                yield {
                    "summary":    output.get("summary", ""),
                    "milestones": output.get("milestones", []),
                }  # dict
