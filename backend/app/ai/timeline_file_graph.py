"""파일별 타임라인 분석 LangGraph.

단일 노드 구성:
  [summarize_file] ──▶ END

type / domain 힌트를 프롬프트에 삽입해 Bedrock 이 맥락에 맞는 요약을 생성하도록 한다.
  - feat  → '기능 추가의 역사' 관점
  - fix   → '디버깅 및 안정화의 역사' 관점
  - refactor → '구조 개선의 역사' 관점
  (그 외 타입도 동일 패턴으로 힌트 제공)
"""

from typing import TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

from app.core.bedrock import get_bedrock_llm

# ── type → 관점 레이블 매핑 ──────────────────────────────────────────────────

_TYPE_PERSPECTIVE: dict[str, str] = {
    "feat":     "기능 추가의 역사",
    "fix":      "디버깅 및 안정화의 역사",
    "refactor": "구조 개선의 역사",
    "perf":     "성능 최적화의 역사",
    "docs":     "문서화의 역사",
    "style":    "코드 스타일 정리의 역사",
}


def _build_prompt(
    file_path: str,
    commits_text: str,
    commit_type: str,
    commit_domain: str,
) -> str:
    perspective = _TYPE_PERSPECTIVE.get(commit_type, f"'{commit_type}' 작업의 역사")
    domain_hint = f"[{commit_domain}] 도메인 " if commit_domain else ""

    return f"""아래는 {domain_hint}소스 파일 `{file_path}` 의 Git 커밋 이력입니다.
이 파일은 {commit_domain or '알 수 없는'} 도메인의 '{commit_type}' 작업이야.
**{perspective}** 관점으로 변경 흐름을 한국어 3~5문장으로 요약하세요.
기술적 표현보다 기획/비즈니스 의도가 드러나도록 서술하세요.

[커밋 이력]
{commits_text}"""


# ── State ──────────────────────────────────────────────────────────────────────

class FileTimelineState(TypedDict):
    file_path:     str
    commits_text:  str          # 커밋 목록 포맷 문자열
    commit_type:   str          # 파싱된 커밋 타입 (feat / fix / ...)
    commit_domain: str          # 파싱된 도메인 (auth / payment / ...)
    summary:       str | None   # Bedrock 요약 결과


# ── 노드 ──────────────────────────────────────────────────────────────────────

def summarize_file(state: FileTimelineState) -> FileTimelineState:
    """type/domain 힌트를 포함해 Bedrock 으로 파일 요약을 생성한다."""
    llm = get_bedrock_llm(max_tokens=600)
    prompt = _build_prompt(
        file_path=state["file_path"],
        commits_text=state["commits_text"],
        commit_type=state["commit_type"],
        commit_domain=state["commit_domain"],
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    return {**state, "summary": response.content.strip()}


# ── 그래프 빌드 ────────────────────────────────────────────────────────────────

def _build_graph():
    g = StateGraph(FileTimelineState)
    g.add_node("summarize_file", summarize_file)
    g.set_entry_point("summarize_file")
    g.add_edge("summarize_file", END)
    return g.compile()


_FILE_GRAPH = _build_graph()


async def run_file_timeline_graph(
    file_path: str,
    commits_text: str,
    commit_type: str,
    commit_domain: str,
) -> str:
    """파일 커밋 이력 + 타입/도메인 힌트로 Bedrock 요약을 비동기 생성한다.

    ainvoke() 로 이벤트 루프를 블로킹하지 않는다.
    실패 시 예외를 그대로 올린다 — 호출 측에서 처리.
    """
    final: FileTimelineState = await _FILE_GRAPH.ainvoke({
        "file_path":     file_path,
        "commits_text":  commits_text,
        "commit_type":   commit_type,
        "commit_domain": commit_domain,
        "summary":       None,
    })

    summary = final.get("summary")
    if not summary:
        raise RuntimeError(f"LangGraph 가 요약을 반환하지 않았습니다 — {file_path}")
    return summary
