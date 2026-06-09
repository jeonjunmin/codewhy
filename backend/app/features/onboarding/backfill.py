"""과거 커밋 백본 적재 (브라운필드 온보딩).

레거시 레포 전체 git 히스토리를 훑어 commits/files/commit_files 공유 백본에 upsert 한다.
GitHub Issue 연동 방식으로 전환한 이후 document_links 사전 생성은 더 이상 필요 없다.
(코드 라인 → git blame → commit → PR → GitHub Issue 체인을 요청 시점에 실시간 조회)

흐름:
  1. get_or_create_repository / upsert_commit / get_or_create_file / link_commit_file 로 백본 적재
  2. 커밋마다 commit_set 내에서 멱등 (UNIQUE 제약으로 재실행 안전)
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import git
from app.core.tickets import extract_ticket
from app.db import crud_common
from app.features.timeline.crud import _parse_date

logger = logging.getLogger(__name__)


async def run_backfill(
    db: AsyncSession,
    repo_path: str,
    *,
    since: str = "",
    limit: int = 0,
) -> dict:
    """레포 전체 히스토리를 공유 백본(commits/files/commit_files)에 적재한다.

    반환: BackfillResponse 필드 dict — commitsScanned, commitsMatched, linksCreated
    linksCreated 는 항상 0 (GitHub Issue 연동은 실시간 조회로 대체).
    """
    repo = await crud_common.get_or_create_repository(db, repo_path)
    log = git.get_repo_log(repo_path, since=since, limit=limit or 0)

    scanned = 0
    for entry in log:
        commit = await crud_common.upsert_commit(
            db,
            repo.id,
            entry["hash"],
            author=entry.get("author"),
            author_email=entry.get("author_email"),
            committed_date=_parse_date(entry.get("date", "")),
            message=entry.get("subject"),
            ticket=extract_ticket(entry.get("subject", "")),
        )
        for f in entry.get("files", []):
            file = await crud_common.get_or_create_file(db, repo.id, f["path"])
            await crud_common.link_commit_file(db, commit.id, file.id, f["added"], f["removed"])
        scanned += 1

    await db.commit()
    logger.info("backfill 완료 — repo=%s  커밋 %d건 적재", repo_path, scanned)
    return {
        "commitsScanned": scanned,
        "commitsMatched": scanned,
        "linksCreated": 0,
    }
