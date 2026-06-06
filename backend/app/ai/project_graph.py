"""프로젝트 전체 요약 LangGraph 엔진.

타임라인 graph.py 와 달리 단일 노드로 구성한다:
  [summarize_project] ──▶ END

입력 State:
  project_data: str  — git 로그 + 파일 목록 텍스트
출력 State:
  summary: str | None — Bedrock 이 생성한 요약 텍스트

ainvoke() 로 호출하면 LangGraph 가 동기 노드를 스레드 풀에서 실행하므로
async FastAPI BackgroundTask 안에서도 이벤트 루프를 블로킹하지 않는다.
"""

from typing import TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

from app.core.bedrock import get_bedrock_llm


# ── State ──────────────────────────────────────────────────────────────────────

class ProjectSummaryState(TypedDict):
    project_data: str          # 수집된 git/파일 정보 텍스트
    summary: str | None        # Bedrock 요약 결과 (노드 실행 후 채워짐)
    error: str | None          # 실패 시 오류 메시지


# ── 노드 ──────────────────────────────────────────────────────────────────────

def summarize_project(state: ProjectSummaryState) -> ProjectSummaryState:
    """Bedrock 으로 프로젝트 전체 요약을 생성한다.

    동기 함수지만 ainvoke() 호출 시 LangGraph 가 자동으로 ThreadPoolExecutor 에서 실행한다.
    """
    llm = get_bedrock_llm(max_tokens=1000)
    prompt = (
        "아래는 소프트웨어 프로젝트의 구조와 최근 커밋 이력입니다.\n"
        "전체 프로젝트의 목적, 주요 기능, 최근 변경 흐름을 한국어로 3~5문장으로 요약하세요.\n\n"
        + state["project_data"]
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    return {**state, "summary": response.content, "error": None}


# ── 그래프 빌드 ────────────────────────────────────────────────────────────────

def _build_graph():
    g = StateGraph(ProjectSummaryState)
    g.add_node("summarize_project", summarize_project)
    g.set_entry_point("summarize_project")
    g.add_edge("summarize_project", END)
    return g.compile()


# 앱 시작 시 한 번만 컴파일
_PROJECT_GRAPH = _build_graph()


async def run_project_summary_graph(project_data: str) -> str:
    """프로젝트 데이터를 받아 Bedrock 요약 텍스트를 반환한다.

    ainvoke() 를 사용해 이벤트 루프를 블로킹하지 않는다.
    실패 시 예외를 그대로 올린다 — tasks.py 에서 잡아서 FAILED 처리.
    """
    final_state: ProjectSummaryState = await _PROJECT_GRAPH.ainvoke({
        "project_data": project_data,
        "summary": None,
        "error": None,
    })

    summary = final_state.get("summary")
    if not summary:
        raise RuntimeError("LangGraph 가 요약을 반환하지 않았습니다.")
    return summary
