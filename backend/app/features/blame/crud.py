"""Context Blame — PostgreSQL 캐시 CRUD.

블레임 응답은 두 출처로 나뉜다:
  - 커밋 메타데이터(commitHash/author/date/ticket/team)  → 공유 백본 commits 행
  - AI 산출물(explanation/aiSuggestion/sourceRef/...)     → blame_explanations 행

캐시 키 = (file_id, commit_id, line_history_hash).
  · line_history_hash='' → 커밋×파일 스코프. "왜 바뀌었나"는 줄이 아니라 커밋이 그 파일에
    가한 변경의 속성이므로, 같은 커밋이 바꾼 여러 줄(단일 리비전)은 설명 1개를 공유한다.
  · line_history_hash=<해시> → 라인 스코프. 여러 번 수정된 줄(멀티 리비전)은 이력 반영 설명을
    줄마다 따로 캐시한다(같은 커밋의 다른 줄에 잘못 적중하지 않게 분리).
라인이 밀려 blamed 커밋이 달라지거나 줄 이력이 바뀌면 매칭 row 가 없어 자동 미스 → 재계산(stale 방지).

👤 담당: 개발자 A
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_team_map
from app.db.models import BlameExplanation, Commit


async def get_cached_blame(
    db: AsyncSession, file_id: int, commit: Commit, line_history_hash: str = ""
) -> dict | None:
    """캐시 적중 시 BlameResponse 형태의 dict 를 재구성해 반환한다(없으면 None).

    line_history_hash='' 면 커밋×파일 스코프, 해시면 라인 스코프(멀티 리비전 줄) 행을 찾는다.
    """
    stmt = select(BlameExplanation).where(
        BlameExplanation.file_id == file_id,
        BlameExplanation.commit_id == commit.id,
        BlameExplanation.line_history_hash == line_history_hash,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None

    return _to_response(row, commit)


async def save_blame(
    db: AsyncSession, file_id: int, commit_id: int, result: dict, line_history_hash: str = ""
) -> None:
    """AI 분석 결과(BlameResponse dict)에서 AI 산출물만 추출해 upsert 한다.

    line_history_hash='' = 커밋 스코프(단일 리비전), 해시 = 라인 스코프(멀티 리비전 줄).
    """
    values = {
        "file_id": file_id,
        "commit_id": commit_id,
        "line_history_hash": line_history_hash,
        "explanation": result.get("explanation", ""),
        "ai_suggestion": result.get("aiSuggestion"),
        "source_ref": result.get("sourceRef"),
        "issue_url": result.get("issueUrl"),
        "attachments": result.get("attachments", []),
        "change_stats": result.get("changeStats"),
        "pr_info": result.get("prInfo"),
        "related_changes": result.get("relatedChanges", []),
    }
    _KEYS = ("file_id", "commit_id", "line_history_hash")
    stmt = (
        pg_insert(BlameExplanation)
        .values(**values)
        .on_conflict_do_update(
            index_elements=list(_KEYS),
            set_={k: v for k, v in values.items() if k not in _KEYS},
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
        "issueUrl": row.issue_url,
        "attachments": row.attachments or [],
        "aiSuggestion": row.ai_suggestion,
        "changeStats": row.change_stats,
        "prInfo": row.pr_info,
        "relatedChanges": row.related_changes or [],
    }
