"""LangGraph 기반 타임라인 요약 엔진 — ②③④ 흐름 담당.

데이터 흐름:
  ② RDS 에서 읽어온 커밋 목록 (list[dict]) 을 입력으로 받는다.
  ③ LangGraph Map-Reduce 파이프라인이 AWS Bedrock(Claude) 에 요약을 요청한다.
  ④ 최종 요약(summary + milestones) 을 dict 로 반환한다.

Map-Reduce 파이프라인 구조:
  [classify_and_split]
        │
        ▼
  [map_summarize]      ← 청크별 중간 요약 (Bedrock 호출 × N)
        │
        ▼
  [reduce_merge]       ← 중간 요약 통합 → 최종 JSON (Bedrock 호출 × 1)
        │
        ▼
  [parse_output] ──성공──▶ END
        │실패
        ├──재시도(MAX_RETRIES)──▶ [increment_retry] ──▶ [reduce_merge]
        └──초과──────────────▶ [apply_fallback]    ──▶ END
"""

import json
import re
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

from app.core.bedrock import get_bedrock_llm

# ── 상수 ─────────────────────────────────────────────────────────────────────

CHUNK_SIZE = 20    # 청크 하나당 최대 커밋 수 (Bedrock 컨텍스트 제한 대응)
MAX_RETRIES = 2    # JSON 파싱 실패 시 reduce_merge 재시도 횟수

_TYPE_LABELS: dict[str, str] = {
    "feat":     "기능 추가",
    "fix":      "버그 수정",
    "refactor": "구조 개선",
    "perf":     "성능 최적화",
    "docs":     "문서 수정",
    "test":     "테스트",
    "chore":    "설정 변경",
}

# 타임라인에서 제외할 커밋 타입 (노이즈 감소)
_SKIP_TYPES = {"test", "chore", "docs"}


# ── 그래프 상태 정의 ──────────────────────────────────────────────────────────

class TimelineState(TypedDict):
    """LangGraph 노드 간에 공유되는 파이프라인 상태."""
    repo_path: str
    file_path: str
    commits: list[dict]          # ② RDS 에서 읽어온 커밋 목록
    chunks: list[list[dict]]     # CHUNK_SIZE 단위로 분할된 묶음
    chunk_summaries: list[str]   # 청크별 중간 요약 (map 결과)
    raw_output: str              # reduce 단계 LLM 원본 출력
    result: dict | None          # 최종 파싱 결과 (④ 반환값)
    retry_count: int             # reduce 재시도 횟수


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _classify_commit(commit: dict) -> dict:
    """<Type>[Domain]: <Description> 형식을 파싱해 타입/도메인을 추출한다."""
    match = re.match(r'^(\w+)(?:\[([^\]]+)\])?:\s*(.+)$', commit["subject"])
    if match:
        return {
            **commit,
            "type":        match.group(1).lower(),
            "domain":      match.group(2) or "",
            "description": match.group(3),
        }
    return {**commit, "type": "unknown", "domain": "", "description": commit["subject"]}


def _format_commits_for_prompt(commits: list[dict]) -> str:
    """커밋 목록을 LLM 프롬프트용 텍스트로 변환한다."""
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


# ── 노드 정의 ─────────────────────────────────────────────────────────────────

def classify_and_split(state: TimelineState) -> TimelineState:
    """노드 1 — 커밋 분류 및 청크 분할.

    - 타입별로 커밋을 분류하고 노이즈성 타입(test/chore/docs)을 제거한다.
    - 남은 커밋을 CHUNK_SIZE 단위로 분할해 map 단계를 준비한다.
    """
    classified = [_classify_commit(c) for c in state["commits"]]
    meaningful = [c for c in classified if c["type"] not in _SKIP_TYPES]
    target = meaningful or classified   # 의미 있는 커밋이 없으면 전체 사용

    chunks = [target[i: i + CHUNK_SIZE] for i in range(0, len(target), CHUNK_SIZE)]
    return {**state, "commits": target, "chunks": chunks, "chunk_summaries": []}


def map_summarize(state: TimelineState) -> TimelineState:
    """노드 2 — 청크별 중간 요약 (Map 단계).

    ③ 각 청크에 대해 Bedrock(Claude) 호출 → 자연어 요약 생성.
    청크 수만큼 Bedrock 을 순차 호출한다. (추후 Send() API로 병렬화 가능)
    """
    llm = get_bedrock_llm(max_tokens=400)
    summaries: list[str] = []

    for chunk in state["chunks"]:
        prompt = (
            "다음 커밋들의 핵심 변경사항을 2~3문장으로 요약하세요. "
            "JSON이 아닌 자연어로 작성하세요.\n\n"
            + _format_commits_for_prompt(chunk)
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        summaries.append(response.content)

    return {**state, "chunk_summaries": summaries}


def reduce_merge(state: TimelineState) -> TimelineState:
    """노드 3 — 최종 타임라인 JSON 생성 (Reduce 단계).

    ③ 청크별 중간 요약을 하나로 합쳐 Bedrock 에 최종 JSON 생성 요청.
    JSON 파싱 실패 시 parse_output 에서 이 노드를 재시도한다.
    """
    llm = get_bedrock_llm(max_tokens=1000)

    sections = "\n\n".join(
        f"[구간 {i + 1}]\n{s}" for i, s in enumerate(state["chunk_summaries"])
    )

    prompt = f"""아래는 한 파일의 변경 이력을 구간별로 요약한 내용입니다.
전체를 종합하여 JSON 형식으로만 응답하세요. JSON 외 다른 텍스트는 출력하지 마세요.

구간별 요약:
{sections}

전체 커밋 (마일스톤 날짜 참고용):
{_format_commits_for_prompt(state["commits"][:30])}

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
    """노드 4 — JSON 파싱.

    ④ LLM 원본 출력에서 JSON 블록을 추출한다.
    실패 시 result=None 을 설정해 조건부 엣지가 재시도/폴백을 선택하도록 한다.
    """
    try:
        match = re.search(r'\{.*\}', state["raw_output"], re.DOTALL)
        parsed = json.loads(match.group() if match else state["raw_output"])
        return {**state, "result": parsed}
    except (json.JSONDecodeError, AttributeError):
        return {**state, "result": None}


def increment_retry(state: TimelineState) -> TimelineState:
    """노드 5 — 재시도 카운터 증가."""
    return {**state, "retry_count": state["retry_count"] + 1}


def apply_fallback(state: TimelineState) -> TimelineState:
    """노드 6 — 폴백.

    MAX_RETRIES 초과 시 LLM 원본 출력의 앞 300자 또는 최근 커밋 5개로
    기본 응답을 구성한다. 항상 결과를 반환해 사용자가 빈 화면을 보지 않게 한다.
    """
    commits = state["commits"]
    summary = (
        state["raw_output"].strip()[:300]
        if state.get("raw_output")
        else "요약을 생성할 수 없습니다."
    )
    milestones = [
        {"date": c["date"], "description": c.get("description", c["subject"])}
        for c in commits[:5]
    ]
    return {**state, "result": {"summary": summary, "milestones": milestones}}


# ── 조건부 엣지 ───────────────────────────────────────────────────────────────

def _route_after_parse(state: TimelineState) -> str:
    """parse_output 이후 다음 노드를 결정한다."""
    if state["result"] is not None:
        return "success"
    if state["retry_count"] < MAX_RETRIES:
        return "retry"
    return "fallback"


# ── 그래프 조립 ───────────────────────────────────────────────────────────────

def _build_graph() -> StateGraph:
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
        {"success": END, "retry": "increment_retry", "fallback": "apply_fallback"},
    )
    g.add_edge("increment_retry", "reduce_merge")   # reduce 만 재시도
    g.add_edge("apply_fallback",  END)

    return g.compile()


# 앱 시작 시 한 번만 컴파일 (StateGraph 컴파일은 비용이 있음)
_GRAPH = _build_graph()


def run_timeline_graph(repo_path: str, file_path: str, commits: list[dict]) -> dict:
    """② RDS 커밋 목록을 받아 ④ 최종 요약 dict 를 반환한다.

    Args:
        repo_path: 레포 식별자 (로깅/캐시 키용)
        file_path: 파일 경로
        commits:   RDS 에서 조회한 커밋 목록 [{"hash","author","date","subject"}, ...]

    Returns:
        {"summary": str, "milestones": [{"date": str, "description": str}, ...]}
    """
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
