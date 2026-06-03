"""Timeline Summary 비즈니스 로직.

데이터 흐름:
  ① 확장이 보낸 commits
  → ② PostgreSQL 백본에 upsert + 파일 전체 이력 조회 (crud.py)
  → ③ commit_set_hash 로 요약 캐시 조회 — 적중 시 즉시 반환
  → ④ 미스 시 LangGraph + Bedrock 요약 (app/ai/graph.py) 후 캐시에 저장
  → ⑤ 결과 반환

👤 담당: 개발자 B
"""

import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.graph import run_timeline_graph
from app.features.timeline import crud


def compute_commit_set_hash(commits: list[dict]) -> str:
    """파일의 커밋 목록으로부터 타임라인 요약 캐시 키(SHA-256 hex)를 계산한다.

    ⭐ 이 함수의 구현이 캐시 '무효화 민감도'를 결정한다 — 직접 구현해 주세요(5~10줄).

    핵심 선택지(어떤 데이터를 해시에 넣느냐):
      - 커밋 해시 목록만  → 새 커밋이 생길 때만 재요약 (가장 보편적, 비용 최소)
      - 해시 + 정렬 순서  → 순서까지 반영하려면 정렬 후 join
      - 해시 + 메시지     → 커밋 메시지가 amend 로 바뀌어도 재요약(민감도↑, 적중률↓)

    구현 힌트:
      1) commits 에서 안정적인 식별자(예: c["hash"])를 뽑아
      2) 순서 의존을 없애려면 sorted(...) 로 정렬한 뒤
      3) "\n".join(...) 으로 직렬화하고
      4) hashlib.sha256(serialized.encode()).hexdigest() 를 반환하세요.

    commits 형식: [{"hash","author","date","subject"}, ...]
    """
    # TODO(개발자 B): 위 가이드에 따라 캐시 키 해시를 구현하세요.
    raise NotImplementedError("compute_commit_set_hash 를 구현해 주세요 (timeline/service.py)")


async def summarize(
    db: AsyncSession, repo_path: str, file_path: str, commits: list[dict]
) -> dict:
    # ② 백본 upsert + 파일 전체 이력 조회
    file = await crud.upsert_commits(db, repo_path, file_path, commits)
    stored = await crud.get_commits(db, file.id)
    if not stored:
        raise ValueError("저장된 커밋 이력이 없습니다.")

    # ③ 요약 캐시 조회 (커밋 집합이 동일하면 Bedrock 재호출 없이 반환)
    set_hash = compute_commit_set_hash(stored)
    cached = await crud.get_cached_summary(db, file.id, set_hash)
    if cached:
        return {"summary": cached.summary, "milestones": cached.milestones or []}

    # ④ 미스 → LangGraph(Bedrock) 요약 후 캐시에 저장
    result = run_timeline_graph(repo_path, file_path, stored)
    await crud.save_summary(db, file.id, set_hash, result)
    return result
