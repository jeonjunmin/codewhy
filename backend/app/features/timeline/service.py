"""Timeline Summary 비즈니스 로직.

데이터 흐름:
  ① 확장이 보낸 commits → ② RDS upsert 후 전체 이력 조회 → ③ LangGraph(Bedrock) 요약

👤 담당: 개발자 B
"""

from app.ai.graph import run_timeline_graph          # ③④ LangGraph + Bedrock 엔진
from app.db.postgres import AsyncSessionLocal
from app.features.timeline import crud


async def summarize(repo_path: str, file_path: str, commits: list[dict]) -> dict:
    # ② DB에서 깃 로그 읽어오기 (upsert 후 전체 이력 SELECT)
    async with AsyncSessionLocal() as db:
        await crud.upsert_commits(db, repo_path, file_path, commits)
        stored = await crud.get_commits(db, repo_path, file_path)

    if not stored:
        raise ValueError("저장된 커밋 이력이 없습니다.")

    # ③ Bedrock(LangGraph Map-Reduce)으로 요약
    return run_timeline_graph(repo_path, file_path, stored)
