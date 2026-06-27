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

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_team_map
from app.db.models import BlameExplanation, Commit, File, Repository

import logging

logger = logging.getLogger(__name__)


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


async def clear_explanations_for_file(
    db: AsyncSession, repo_path: str, file_path: str
) -> int:
    """현재 파일의 돋보기 설명 캐시(모든 커밋·라인)를 삭제하고 삭제된 행 수를 반환한다.

    시연 등에서 '이미 분석한 줄을 다시 분석'하려고 비울 때 쓴다. 다음 돋보기 조회 때
    캐시 미스가 나면서 최신 형식으로 재생성된다(타임라인 clear_summaries_for_file 와 동일 발상).

    레포/파일 행은 '조회만' 한다(없으면 만들지 않음) — 캐시 비우기가 백본에 빈 행을 남기지 않게.
    file_path 는 분석 저장 때와 동일하게 라우터가 정규화 없이 넘긴 값을 그대로 키로 쓴다.
    """
    repo = (
        await db.execute(select(Repository).where(Repository.identifier == repo_path))
    ).scalar_one_or_none()
    if repo is None:
        return 0

    file = (
        await db.execute(
            select(File).where(File.repo_id == repo.id, File.file_path == file_path)
        )
    ).scalar_one_or_none()
    if file is None:
        return 0

    result = await db.execute(
        delete(BlameExplanation).where(BlameExplanation.file_id == file.id)
    )
    await db.commit()
    deleted = result.rowcount or 0
    logger.info("[crud] clear_explanations_for_file — file_id=%d 삭제=%d건", file.id, deleted)
    return deleted


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
