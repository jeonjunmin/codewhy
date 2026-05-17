"""Timeline Summary LangGraph 파이프라인.

Map-Reduce 패턴:
  1. classify_and_split  — 커밋 분류 + 청크 분할
  2. map_summarize       — 청크별 중간 요약
  3. reduce_merge        — 중간 요약 → 최종 타임라인 JSON
  4. parse_output        — JSON 파싱 (실패 시 재시도/폴백)

👤 담당: 개발자 B
"""

import json
import re
from typing import TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

from app.core.config import get_anthropic_api_key

# ── 상수 ────────────────────────────────────────────────────────────────────

CHUNK_SIZE = 20   # 청크 하나당 최대 커밋 수
MAX_RETRIES = 2   # JSON 파싱 실패 시 reduce 재시도 횟수

_TYPE_LABELS: dict[str, str] = {
    "feat":     "기능 추가",
    "fix":      "버그 수정",
    "refactor": "구조 개선",
    "perf":     "성능 최적화",
    "docs":     "문서 수정",
    "test":     "테스트",
    "chore":    "설정 변경",
}

# 타임라인 집계에서 제외할 타입
_SKIP_TYPES = {"test", "chore", "docs"}


# ── 상태 정의 ────────────────────────────────────────────────────────────────

class TimelineState(TypedDict):
    repo_path: str
    file_path: str
    commits: list[dict]          # 분류된 전체 커밋 목록
    chunks: list[list[dict]]     # CHUNK_SIZE 단위로 분할된 커밋 묶음
    chunk_summaries: list[str]   # 청크별 중간 요약 텍스트
    raw_output: str              # reduce 단계 LLM 원본 출력
    result: dict | None          # 파싱 성공 시 최종 결과
    retry_count: int             # reduce 재시도 횟수


# ── LLM 팩토리 ───────────────────────────────────────────────────────────────

def _get_llm(max_tokens: int = 800) -> ChatAnthropic:
    return ChatAnthropic(
        model="claude-opus-4-7",
        api_key=get_anthropic_api_key(),
        max_tokens=max_tokens,
    )


# ── 헬퍼 ────────────────────────────────────────────────────────────────────

def _classify_commit(commit: dict) -> dict:
    """<Type>[Domain]: <Description> 형식에서 필드를 추출한다."""
    match = re.match(r'^(\w+)(?:\[([^\]]+)\])?:\s*(.+)$', commit["subject"])
    if match:
        return {
            **commit,
            "type": match.group(1).lower(),
            "domain": match.group(2) or "",
            "description": match.group(3),
        }
    return {**commit, "type": "unknown", "domain": "", "description": commit["subject"]}


def _format_commits(commits: list[dict]) -> str:
    return "\n".join(
        "- [{date}] {label}{domain}: {desc} (by {author})".format(
            date=c["date"],
            label=_TYPE_LABELS.get(c.get("type", ""), c.get("type", "")),
            domain=f" [{c['domain']}]" if c.get("domain") else "",
            desc=c.get("description", c["subject"]),
            author=c["author"],
        )
        for c in commits
    )


# ── 노드 ────────────────────────────────────────────────────────────────────

def classify_and_split(state: TimelineState) -> TimelineState:
    """커밋 타입을 분류하고 CHUNK_SIZE 단위로 분할한다."""
    classified = [_classify_commit(c) for c in state["commits"]]
    meaningful = [c for c in classified if c["type"] not in _SKIP_TYPES]
    target = meaningful or classified

    chunks = [target[i : i + CHUNK_SIZE] for i in range(0, len(target), CHUNK_SIZE)]
    return {**state, "commits": target, "chunks": chunks, "chunk_summaries": []}


def map_summarize(state: TimelineState) -> TimelineState:
    """각 청크를 개별 요약한다 (순차 실행; 추후 Send()로 병렬화 가능)."""
    llm = _get_llm(max_tokens=400)
    summaries: list[str] = []

    for chunk in state["chunks"]:
        prompt = (
            "다음 커밋들의 핵심 변경사항을 2~3문장으로 요약하세요. "
            "JSON이 아닌 자연어로 작성하세요.\n\n"
            + _format_commits(chunk)
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        summaries.append(response.content)

    return {**state, "chunk_summaries": summaries}


def reduce_merge(state: TimelineState) -> TimelineState:
    """청크 요약들을 합쳐 최종 타임라인 JSON을 생성한다."""
    llm = _get_llm(max_tokens=1000)

    sections = "\n\n".join(
        f"[구간 {i + 1}]\n{s}" for i, s in enumerate(state["chunk_summaries"])
    )
    milestone_hint = _format_commits(state["commits"][:30])

    prompt = f"""아래는 한 파일의 변경 이력을 구간별로 요약한 내용입니다.
전체를 종합하여 JSON 형식으로만 응답하세요. JSON 외 다른 텍스트는 출력하지 마세요.

구간별 요약:
{sections}

전체 커밋 (마일스톤 날짜 참고용):
{milestone_hint}

응답 형식:
{{
  "summary": "파일 전체 역사를 2~3문장으로 요약",
  "milestones": [
    {{"date": "YYYY-MM-DD", "description": "주요 변경 내용 한 줄"}}
  ]
}}"""

    response = llm.invoke([HumanMessage(content=prompt)])
    return {**state, "raw_output": response.content}


def parse_output(state: TimelineState) -> TimelineState:
    """raw_output에서 JSON을 파싱한다. 실패 시 result=None."""
    try:
        match = re.search(r'\{.*\}', state["raw_output"], re.DOTALL)
        parsed = json.loads(match.group() if match else state["raw_output"])
        return {**state, "result": parsed}
    except (json.JSONDecodeError, AttributeError):
        return {**state, "result": None}


def increment_retry(state: TimelineState) -> TimelineState:
    return {**state, "retry_count": state["retry_count"] + 1}


def apply_fallback(state: TimelineState) -> TimelineState:
    """파싱이 끝내 실패하면 커밋 목록으로 기본 응답을 만든다."""
    commits = state["commits"]
    return {
        **state,
        "result": {
            "summary": (
                state["raw_output"].strip()[:300]
                if state.get("raw_output")
                else "요약을 생성할 수 없습니다."
            ),
            "milestones": [
                {"date": c["date"], "description": c.get("description", c["subject"])}
                for c in commits[:5]
            ],
        },
    }


# ── 조건부 엣지 ──────────────────────────────────────────────────────────────

def _route_after_parse(state: TimelineState) -> str:
    if state["result"] is not None:
        return "success"
    if state["retry_count"] < MAX_RETRIES:
        return "retry"
    return "fallback"


# ── 그래프 빌드 ──────────────────────────────────────────────────────────────

def _build_graph():
    g = StateGraph(TimelineState)

    g.add_node("classify_and_split", classify_and_split)
    g.add_node("map_summarize",      map_summarize)
    g.add_node("reduce_merge",       reduce_merge)
    g.add_node("parse_output",       parse_output)
    g.add_node("increment_retry",    increment_retry)
    g.add_node("apply_fallback",     apply_fallback)

    g.set_entry_point("classify_and_split")
    g.add_edge("classify_and_split", "map_summarize")
    g.add_edge("map_summarize",      "reduce_merge")
    g.add_edge("reduce_merge",       "parse_output")
    g.add_conditional_edges(
        "parse_output",
        _route_after_parse,
        {
            "success":  END,
            "retry":    "increment_retry",
            "fallback": "apply_fallback",
        },
    )
    g.add_edge("increment_retry", "reduce_merge")  # reduce만 재시도
    g.add_edge("apply_fallback",  END)

    return g.compile()


# 앱 시작 시 한 번만 컴파일
_GRAPH = _build_graph()


def run_timeline_graph(repo_path: str, file_path: str, commits: list[dict]) -> dict:
    """그래프를 실행하고 최종 result를 반환한다."""
    final_state: TimelineState = _GRAPH.invoke({
        "repo_path":       repo_path,
        "file_path":       file_path,
        "commits":         commits,
        "chunks":          [],
        "chunk_summaries": [],
        "raw_output":      "",
        "result":          None,
        "retry_count":     0,
    })
    return final_state["result"]
