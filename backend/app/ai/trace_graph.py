"""Requirement Trace — LangGraph 조건부 폴백 StateGraph.

코드 라인에서 연관 기획 문서(GitHub Issue)를 찾는 3단 폴백 전략을, 순차 try/except 대신
**명명된 노드 + 조건부 엣지**로 표현한다. Mermaid 로 그리면 "왜 이 matchType 이 나왔는가"가
한눈에 보인다(에이전트형 폴백 전략 시각화).

  resolve → try_issue ─[found]→ format → END
                       └[empty]→ try_ticket ─[found]→ format → END
                                              └[empty]→ try_semantic → format → END

신뢰도(issue=None / ticket=0.8 / semantic=0.5)와 결과 포맷은 기존
`traceability/service._format_results` 를 그대로 재사용한다. 스트리밍 없음 → ainvoke.

그래프 구조를 보려면:  python -m app.ai.trace_graph   (Mermaid 출력)
"""

import asyncio
import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.core import git, vcs
from app.core.tickets import extract_ticket
from app.features.blame.service import extract_keywords
from app.features.traceability import service as trace_svc

logger = logging.getLogger(__name__)


class TraceState(TypedDict, total=False):
    repo_path: str
    file_path: str
    line: int
    info: Any            # git.BlameInfo | None
    branch: str
    remote: Any          # vcs.Remote | None
    issues: list
    match_type: str
    results: list[dict]


# ── 노드 ───────────────────────────────────────────────────────────────────────

async def resolve_commit(state: TraceState) -> dict:
    """blamed 커밋 + 원격 호스트 식별. 둘 중 하나라도 없으면 빈 결과로 단락한다."""
    try:
        info = await asyncio.to_thread(
            git.get_blame_info, state["repo_path"], state["file_path"], state["line"]
        )
    except Exception:
        return {"info": None, "results": []}
    branch = await asyncio.to_thread(_safe_branch, state["repo_path"])
    remote = await asyncio.to_thread(vcs.detect_remote, state["repo_path"])
    return {"info": info, "branch": branch, "remote": remote, "results": []}


def _safe_branch(repo_path: str) -> str:
    try:
        return git.get_current_branch(repo_path)
    except Exception:
        return ""


def route_after_resolve(state: TraceState) -> str:
    if state.get("info") is None or state.get("remote") is None:
        return END
    return "try_issue"


async def try_issue(state: TraceState) -> dict:
    """1순위 — PR 본문 → Issue 직접 연결(첨부 있음). 확정 매칭."""
    info, remote = state["info"], state["remote"]
    try:
        pr = await asyncio.to_thread(vcs.find_pr_for_commit, state["repo_path"], info.commit_hash)
        if pr and pr.body:
            issues = await asyncio.to_thread(vcs.find_issues_from_pr_body, remote, pr.body)
            if issues:
                return {"issues": issues, "match_type": "issue"}
    except Exception:
        pass
    return {"issues": [], "match_type": ""}


async def try_ticket(state: TraceState) -> dict:
    """2순위 — 커밋 메시지 티켓 번호(PAY-2041)로 Issue 검색. 높음(0.8)."""
    info, remote = state["info"], state["remote"]
    ticket = extract_ticket(info.message, state.get("branch", ""))
    if ticket:
        issues = await asyncio.to_thread(vcs.search_github_issues, remote, ticket)
        if issues:
            return {"issues": issues, "match_type": "ticket"}
    return {"issues": [], "match_type": ""}


async def try_semantic(state: TraceState) -> dict:
    """3순위 — 커밋 키워드로 관련 Issue 시맨틱 검색. 추정(0.5)."""
    info, remote = state["info"], state["remote"]
    keywords = extract_keywords(info.message)
    if keywords:
        query = " ".join(keywords[:5])
        issues = await asyncio.to_thread(vcs.search_github_issues, remote, query, 3)
        if issues:
            return {"issues": issues, "match_type": "semantic"}
    return {"issues": [], "match_type": ""}


def format_results(state: TraceState) -> dict:
    """Issue 목록 → DocumentMatch dict 리스트(기존 서비스 포맷터 재사용)."""
    results = trace_svc._format_results(state.get("issues", []), state.get("match_type", ""))
    return {"results": results}


# ── 그래프 빌드/컴파일 ──────────────────────────────────────────────────────────

def _build_graph():
    builder = StateGraph(TraceState)
    builder.add_node("resolve_commit", resolve_commit)
    builder.add_node("try_issue", try_issue)
    builder.add_node("try_ticket", try_ticket)
    builder.add_node("try_semantic", try_semantic)
    builder.add_node("format_results", format_results)

    builder.add_edge(START, "resolve_commit")
    builder.add_conditional_edges("resolve_commit", route_after_resolve, ["try_issue", END])
    # 각 폴백 단계: 결과 있으면 format, 없으면 다음 단계로
    builder.add_conditional_edges(
        "try_issue", lambda s: "format_results" if s.get("issues") else "try_ticket",
        ["format_results", "try_ticket"],
    )
    builder.add_conditional_edges(
        "try_ticket", lambda s: "format_results" if s.get("issues") else "try_semantic",
        ["format_results", "try_semantic"],
    )
    builder.add_edge("try_semantic", "format_results")
    builder.add_edge("format_results", END)
    return builder.compile()


trace_graph = _build_graph()


async def atrace(repo_path: str, file_path: str, line: int) -> list[dict]:
    """그래프를 끝까지 돌려 DocumentMatch dict 리스트를 반환한다(라우터가 호출)."""
    out = await trace_graph.ainvoke(
        {"repo_path": repo_path, "file_path": file_path, "line": line}
    )
    return out.get("results", [])


if __name__ == "__main__":  # python -m app.ai.trace_graph  → Mermaid 다이어그램
    print(trace_graph.get_graph().draw_mermaid())
