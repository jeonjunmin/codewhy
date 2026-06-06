"""Timeline Summary 비즈니스 로직.

데이터 흐름:
  ① 확장이 보낸 commits
  → ② PostgreSQL upsert 후 전체 이력 조회 (crud.py)
  → ③ LangGraph Map-Reduce + Bedrock 요약 (app/ai/graph.py)
  → ④ 결과 반환

👤 담당: 개발자 B
"""

from app.ai.graph import run_timeline_graph
from app.features.timeline import crud


async def summarize(repo_path: str, file_path: str, commits: list[dict]) -> dict:
    # ① timeline_summaries DB 캐시 먼저 조회 — 있으면 Bedrock 재호출 없이 바로 반환
    cached = await crud.get_timeline_summary_by_path(repo_path, file_path)
    if cached:
        return cached

    # ② PostgreSQL에 upsert 후 전체 이력 조회
    await crud.upsert_commits(repo_path, file_path, commits)
    stored = await crud.get_commits(repo_path, file_path)

    if not stored:
        raise ValueError("저장된 커밋 이력이 없습니다.")

    # ③ LangGraph(Bedrock) Map-Reduce 요약
    return run_timeline_graph(repo_path, file_path, stored)
