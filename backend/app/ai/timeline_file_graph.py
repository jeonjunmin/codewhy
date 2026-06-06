"""파일별 타임라인 분석 LangGraph.

단일 노드 구성:
  [summarize_file] ──▶ END

Bedrock 이 summary + milestones 를 JSON 으로 반환하도록 프롬프트를 구성한다.
JSON 파싱 실패 시 전체 텍스트를 summary 로, milestones 는 [] 로 폴백한다.
"""

import json
import re
from typing import Any, TypedDict

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
**{perspective}** 관점으로 변경 흐름을 분석하세요.

반드시 아래 JSON 형식으로만 응답하세요. 코드블록, 마크다운, 추가 설명 없이 JSON 만 반환하세요:
{{
  "summary": "이 파일의 변경 흐름 요약 (한국어 3~5문장, 기획/비즈니스 의도 중심)",
  "milestones": [
    {{"date": "YYYY-MM-DD", "description": "주요 변경 내용 한 줄"}},
    {{"date": "YYYY-MM-DD", "description": "주요 변경 내용 한 줄"}}
  ]
}}

milestones 는 커밋 이력에서 중요한 변경점 2~5개를 날짜순으로 추출하세요.

[커밋 이력]
{commits_text}"""


# ── State ──────────────────────────────────────────────────────────────────────

class FileTimelineState(TypedDict):
    file_path:     str
    commits_text:  str
    commit_type:   str
    commit_domain: str
    summary:       str | None


# ── 노드 ──────────────────────────────────────────────────────────────────────

def summarize_file(state: FileTimelineState) -> FileTimelineState:
    """type/domain 힌트를 포함해 Bedrock 으로 파일 요약을 생성한다."""
    llm = get_bedrock_llm(max_tokens=800)
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


def _parse_ai_response(raw: str) -> dict[str, Any]:
    """Bedrock 응답 문자열에서 summary + milestones 를 추출한다.

    JSON 파싱 성공 시 그대로 반환, 실패 시 raw 텍스트를 summary 로 폴백한다.
    """
    # 코드블록 마커 제거 (```json ... ``` 형태 대응)
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()

    try:
        data = json.loads(cleaned)
        summary    = str(data.get("summary", "")).strip() or raw
        milestones = data.get("milestones", [])
        # milestones 각 항목을 {"date": str, "description": str} 형식으로 정제
        if isinstance(milestones, list):
            milestones = [
                {
                    "date":        str(m.get("date", "")),
                    "description": str(m.get("description", "")),
                }
                for m in milestones
                if isinstance(m, dict)
            ]
        else:
            milestones = []
        return {"summary": summary, "milestones": milestones}
    except (json.JSONDecodeError, Exception):
        return {"summary": raw, "milestones": []}


async def run_file_timeline_graph(
    file_path: str,
    commits_text: str,
    commit_type: str,
    commit_domain: str,
) -> dict[str, Any]:
    """파일 커밋 이력 + 타입/도메인 힌트로 Bedrock 요약과 마일스톤을 비동기 생성한다.

    Returns:
        {"summary": str, "milestones": list[{"date": str, "description": str}]}
    """
    final: FileTimelineState = await _FILE_GRAPH.ainvoke({
        "file_path":     file_path,
        "commits_text":  commits_text,
        "commit_type":   commit_type,
        "commit_domain": commit_domain,
        "summary":       None,
    })

    raw = final.get("summary") or ""
    if not raw:
        raise RuntimeError(f"LangGraph 가 요약을 반환하지 않았습니다 — {file_path}")

    return _parse_ai_response(raw)
