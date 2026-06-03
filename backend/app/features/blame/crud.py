"""Context Blame — PostgreSQL 캐시 CRUD.

블레임 응답은 두 출처로 나뉜다:
  - 커밋 메타데이터(commitHash/author/date/ticket/team)  → 공유 백본 commits 행
  - AI 산출물(explanation/aiSuggestion/sourceRef/...)     → blame_explanations 행

캐시 키 = (file_id, line_no, commit_id). 라인이 밀려 blamed 커밋이 달라지면 commit_id 가 바뀌어
매칭 row 가 없으므로 자동 캐시 미스 → 재계산된다(stale 응답 방지).

👤 담당: 개발자 A
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_team_map
from app.db.models import BlameExplanation, Commit


async def get_cached_blame(db: AsyncSession, file_id: int, line_no: int, commit: Commit) -> dict | None:
    """캐시 적중 시 BlameResponse 형태의 dict 를 재구성해 반환한다(없으면 None)."""
    stmt = select(BlameExplanation).where(
        BlameExplanation.file_id == file_id,
        BlameExplanation.line_no == line_no,
        BlameExplanation.commit_id == commit.id,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None

    return _to_response(row, commit)


async def save_blame(
    db: AsyncSession, file_id: int, line_no: int, commit_id: int, result: dict
) -> None:
    """AI 분석 결과(BlameResponse dict)에서 AI 산출물만 추출해 upsert 한다."""
    values = {
        "file_id": file_id,
        "line_no": line_no,
        "commit_id": commit_id,
        "explanation": result.get("explanation", ""),
        "ai_suggestion": result.get("aiSuggestion"),
        "source_ref": result.get("sourceRef"),
        "change_stats": result.get("changeStats"),
        "pr_info": result.get("prInfo"),
        "related_changes": result.get("relatedChanges", []),
    }
    stmt = (
        pg_insert(BlameExplanation)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["file_id", "line_no", "commit_id"],
            set_={k: v for k, v in values.items() if k not in ("file_id", "line_no", "commit_id")},
        )
    )
    await db.execute(stmt)
    await db.commit()


def _to_response(row: BlameExplanation, commit: Commit) -> dict:
    """blame_explanations(AI) + commits(메타데이터) 를 합쳐 BlameResponse dict 로 만든다."""
    source_ref = row.source_ref
    return {
        "explanation": row.explanation,
        "commitHash": commit.commit_hash,
        "author": commit.author or "",
        "date": commit.committed_date.isoformat() if commit.committed_date else "",
        "ticket": commit.ticket,
        "team": get_team_map().get(commit.author or ""),
        "sourceRef": source_ref,
        "specRef": source_ref,
        "aiSuggestion": row.ai_suggestion,
        "changeStats": row.change_stats,
        "prInfo": row.pr_info,
        "relatedChanges": row.related_changes or [],
    }
