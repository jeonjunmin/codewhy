"""Context Blame — LangGraph StateGraph 오케스트레이션.

블레임 파이프라인(git blame → PR/이슈/후속커밋 → 컨텍스트 → LLM 설명 → 조립)을
LangGraph `StateGraph` 로 선언적으로 표현한다. 각 노드는 `features/blame/service.py` 의
기존 헬퍼를 얇게 래핑할 뿐이며(로직 재작성 없음), 그래프는 다음을 한 곳에서 보여준다:

  · 조건 분기   — 노이즈 커밋(test/chore/docs)은 LLM/GitHub 호출 없이 즉시 응답
  · 병렬 fan-out — PR 조회와 후속 커밋 조회를 동시에 실행
  · fan-in       — 두 갈래(이슈, 후속커밋)가 모두 끝나야 컨텍스트 빌드
  · 스트리밍     — explain 노드의 LLM 토큰을 astream_events 로 실시간 전달
  · 폴백         — Bedrock 실패 시 degraded 응답으로 합류(캐시 미저장)

스트리밍 UX(SSE meta/delta/done 3프레임)는 `stream_blame_graph` 가 그래프를
`astream_events(version="v2")` 로 돌려 그대로 재현한다 — 프론트 파서는 무수정.

그래프 구조를 보려면:  python -m app.ai.blame_graph   (Mermaid 출력)
"""

import asyncio
import json
import logging
import os
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import git
from app.core.bedrock import get_bedrock_llm
from app.core.commit_classifier import SKIP_TYPES
from app.core.config import get_team_map
from app.features.blame import crud
from app.features.blame import service as svc
from app.db.models import Commit, File

logger = logging.getLogger(__name__)

# explain 노드의 LLM 호출에만 붙이는 태그 — astream_events 에서 이 태그로 토큰을 골라낸다.
# (향후 다른 LLM 호출이 그래프에 추가돼도 블레임 설명 토큰만 스트리밍으로 새지 않게 격리)
_EXPLAIN_TAG = "blame_explain"


# ── State ─────────────────────────────────────────────────────────────────────
# 병렬 노드(fetch_pr / fetch_followups)는 서로 다른 키만 쓰므로 reducer 불필요.
class BlameState(TypedDict, total=False):
    # 입력 (라우터가 채워 넣음 — 중복 git 호출 방지)
    repo_path: str
    file_path: str
    line: int
    info: git.BlameInfo
    branch: str | None
    ticket: str | None
    # 파생
    team: str | None
    commit_type: str
    change_stats: dict
    line_history: list[dict]
    # 외부 조회 (병렬)
    pr: Any
    followups: list[dict]
    issues: list
    # LLM
    context: str
    explanation: str
    degraded: bool
    # 최종 조립 결과 (SSE done 프레임 / analyze 응답이 그대로 쓰는 dict)
    result: dict


# ── 노드 ───────────────────────────────────────────────────────────────────────

async def resolve_commit(state: BlameState) -> dict:
    """team / 변경 통계 / 라인 이력 — git·config 만으로 즉시 구하는 메타."""
    info = state["info"]
    team = get_team_map().get(info.author)
    line_history = await asyncio.to_thread(
        svc._build_line_history, state["repo_path"], state["file_path"], state["line"]
    )
    return {
        "team": team,
        "change_stats": {"added": info.added, "removed": info.removed},
        "line_history": line_history,
    }


def classify(state: BlameState) -> dict:
    """커밋 type 분류(타임라인과 공유하는 commit_classifier 경유)."""
    return {"commit_type": svc._classify_type(state["info"].message)}


def route_after_classify(state: BlameState) -> list[str] | str:
    """노이즈면 정형 응답으로, 아니면 GitHub·후속커밋 조회로 병렬 fan-out."""
    if state["commit_type"] in SKIP_TYPES:
        return "noise_response"
    return ["fetch_github", "fetch_followups"]


def noise_response(state: BlameState) -> dict:
    """노이즈 커밋(test/chore/docs) — Bedrock·GitHub 호출 없이 정형 result."""
    result = svc._noise_response(
        state["info"], state.get("ticket"), state.get("team"), state["commit_type"]
    )
    return {"result": result, "explanation": result["explanation"], "degraded": False}


async def fetch_github(state: BlameState) -> dict:
    """PR → (PR 본문 의존) 연관 이슈를 한 노드에서 순차 조회한다.

    fan-in 정합성: PR·이슈를 별도 super-step 으로 쪼개면 build_context 의 두 부모(이슈 갈래/
    후속커밋 갈래)가 서로 다른 step 에 끝나 build_context 가 두 번 실행된다(→ explain·Bedrock
    중복 호출). PR+이슈를 한 노드로 묶어 fetch_followups 와 같은 super-step 에 끝나게 해 fan-in 을 1회로 만든다.
    """
    pr = await asyncio.to_thread(svc._safe_find_pr, state["repo_path"], state["info"].commit_hash)
    issues = await asyncio.to_thread(
        svc._safe_find_issues, state["repo_path"], pr, state["info"].message
    )
    return {"pr": pr, "issues": issues}


async def fetch_followups(state: BlameState) -> dict:
    followups = await asyncio.to_thread(
        git.find_followup_commits,
        state["repo_path"],
        state.get("ticket"),
        exclude_hash=state["info"].commit_hash,
    )
    return {"followups": followups}


def build_context(state: BlameState) -> dict:
    """설명/후속질문이 공유하는 변경 맥락 블록을 만들고 캐시에 기억시킨다.

    fetch_github 와 fetch_followups 두 incoming 엣지가 모두 끝나야 실행된다(fan-in).
    """
    info = state["info"]
    issues = state.get("issues", [])
    context = svc._build_context(info, issues)
    svc._remember_context(state["repo_path"], state["file_path"], info.commit_hash, context)
    return {"context": context}


async def explain(state: BlameState) -> dict:
    """Bedrock(ChatBedrock)으로 변경 사유를 추론한다.

    astream_events 로 그래프를 돌리면 여기 LLM 토큰이 on_chat_model_stream 으로 새어 나와
    실시간 delta 가 된다. 실패 시 degraded 폴백 문구로 합류(캐시 미저장 신호).
    """
    info = state["info"]
    context = state["context"]
    llm = get_bedrock_llm(max_tokens=300).with_config(tags=[_EXPLAIN_TAG])
    messages = [
        SystemMessage(content=svc._SYSTEM_PROMPT),
        HumanMessage(content=f"{context}\n\n{svc._EXPLAIN_INSTRUCTION}"),
    ]
    try:
        resp = await llm.ainvoke(messages)
        text = (resp.content if isinstance(resp.content, str) else str(resp.content)).strip()
        return {"explanation": text, "degraded": False}
    except Exception as e:
        logger.exception(
            "Bedrock 변경 사유 추론 실패(graph) — commit=%s",
            info.commit_hash[:8] if info.commit_hash else "?",
        )
        return {"explanation": svc._degraded_explanation(info, e), "degraded": True}


def assemble(state: BlameState) -> dict:
    """사이드바 응답 dict 조립 — stream_blame 의 done 페이로드와 동일 스키마."""
    info = state["info"]
    issues = state.get("issues", [])
    pr = state.get("pr")
    related = svc._build_related_changes(issues, pr, state.get("followups", []), state["file_path"])
    source_ref = svc._format_source_ref(issues)
    primary_issue = issues[0] if issues else None
    attachments = [
        {"label": a.label, "url": a.url} for issue in issues for a in issue.attachments
    ]
    result = {
        "explanation": state.get("explanation", ""),
        "aiDegraded": state.get("degraded", False),
        "commitHash": info.commit_hash,
        "author": info.author,
        "date": info.date,
        "ticket": state.get("ticket"),
        "team": state.get("team"),
        "sourceRef": source_ref,
        "specRef": source_ref,
        "issueUrl": primary_issue.url if primary_issue else None,
        "attachments": attachments,
        "changeStats": state.get("change_stats"),
        "prInfo": ({"url": pr.url, "lines": pr.added + pr.removed} if pr else None),
        "relatedChanges": related,
        "lineHistory": state.get("line_history", []),
        "aiSuggestion": None,
    }
    return {"result": result}


# ── 그래프 빌드/컴파일 ──────────────────────────────────────────────────────────

def _build_graph():
    builder = StateGraph(BlameState)
    builder.add_node("resolve_commit", resolve_commit)
    builder.add_node("classify", classify)
    builder.add_node("noise_response", noise_response)
    builder.add_node("fetch_github", fetch_github)
    builder.add_node("fetch_followups", fetch_followups)
    builder.add_node("build_context", build_context)
    builder.add_node("explain", explain)
    builder.add_node("assemble", assemble)

    builder.add_edge(START, "resolve_commit")
    builder.add_edge("resolve_commit", "classify")
    builder.add_conditional_edges(
        "classify",
        route_after_classify,
        ["noise_response", "fetch_github", "fetch_followups"],
    )
    builder.add_edge("noise_response", END)
    # fan-in: GitHub(PR+이슈) 갈래와 후속커밋 갈래가 같은 super-step 에 끝나 build_context 1회 실행
    builder.add_edge("fetch_github", "build_context")
    builder.add_edge("fetch_followups", "build_context")
    builder.add_edge("build_context", "explain")
    builder.add_edge("explain", "assemble")
    builder.add_edge("assemble", END)
    return builder.compile()


blame_graph = _build_graph()


# ── SSE 스트리밍 (그래프를 astream_events 로 구동) ───────────────────────────────

def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


async def stream_blame_graph(
    db: AsyncSession,
    repo_path: str,
    file_path: str,
    line: int,
    *,
    info: git.BlameInfo,
    branch: str | None,
    ticket: str | None,
    commit: Commit | None,
    file: File | None,
):
    """blame_graph 를 SSE(meta/delta/done) 프레임으로 흘려보낸다.

    service.stream_blame 과 시그니처·프레이밍 호환 — 라우터는 호출 대상만 바꾸면 된다.
    """
    team = get_team_map().get(info.author)
    change_stats = {"added": info.added, "removed": info.removed}

    # ① meta — git 만으로 즉시 구하는 메타/라인 이력 (그래프 진입 전 인라인)
    line_history = await asyncio.to_thread(svc._build_line_history, repo_path, file_path, line)
    yield _sse({"meta": {
        "commitHash": info.commit_hash,
        "author": info.author,
        "date": info.date,
        "ticket": ticket,
        "team": team,
        "changeStats": change_stats,
        "lineHistory": line_history,
    }})

    state: BlameState = {
        "repo_path": repo_path, "file_path": file_path, "line": line,
        "info": info, "branch": branch, "ticket": ticket,
        # 메타는 이미 계산했으니 그래프에도 넘겨 resolve_commit 의 재계산을 줄인다.
        "team": team, "change_stats": change_stats, "line_history": line_history,
    }

    # ② 그래프 실행 — explain 노드의 LLM 토큰을 delta 로, 그래프 종료 시 final state 를 회수
    root_run_id = None
    final_state: dict | None = None
    full_text = ""
    async for ev in blame_graph.astream_events(state, version="v2"):
        if root_run_id is None and ev["event"] == "on_chain_start":
            root_run_id = ev["run_id"]
        et = ev["event"]
        if et == "on_chat_model_stream" and _EXPLAIN_TAG in ev.get("tags", []):
            chunk = ev["data"]["chunk"]
            piece = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
            if piece:
                full_text += piece
                yield _sse({"delta": piece})
        elif et == "on_chain_end" and ev.get("run_id") == root_run_id:
            final_state = ev["data"]["output"]

    result = (final_state or {}).get("result", {})
    degraded = bool(result.get("aiDegraded"))

    # ③ degraded/노이즈는 토큰 스트림이 없으므로 본문을 delta 로 한 번 내보낸다.
    if not full_text and result.get("explanation"):
        yield _sse({"delta": result["explanation"]})

    # ④ done — 나머지 필드 확정
    yield _sse({"done": True, **result})

    # ⑤ degraded 가 아니면 캐시 저장 (router/service 와 동일 정책)
    if commit is not None and file is not None and not degraded and result:
        try:
            await crud.save_blame(db, file.id, commit.id, result)
        except Exception:
            logger.warning("blame 캐시 저장 실패 (응답에는 영향 없음)", exc_info=True)


# ── 비스트리밍 호출 (캐시 히트/노이즈/테스트용) ─────────────────────────────────

async def run_blame_graph(
    repo_path: str,
    file_path: str,
    line: int,
    *,
    info: git.BlameInfo,
    branch: str | None,
    ticket: str | None,
) -> dict:
    """그래프를 한 번 끝까지 돌려 result dict 를 반환한다(analyze_blame 동등)."""
    out = await blame_graph.ainvoke({
        "repo_path": repo_path, "file_path": file_path, "line": line,
        "info": info, "branch": branch, "ticket": ticket,
    })
    return out["result"]


if __name__ == "__main__":  # python -m app.ai.blame_graph  → Mermaid 다이어그램
    print(blame_graph.get_graph().draw_mermaid())
