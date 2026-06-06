"""과거 커밋 ↔ 문서 역링크 백필 (브라운필드 온보딩 2단계).

티켓 컨벤션이 없는 레거시 레포는 코드↔문서를 이을 다리가 없다. 그래서 전체 git 히스토리를
한 번 훑어, 각 커밋을 시맨틱 인덱스(KB)로 조회해 가장 잘 맞는 기획 문서를 찾고,
document_links(link_type='commit') 를 **사전에** 만들어 둔다. 조회 시점에는 이 링크를
바로 읽으므로 빠르고 일관적이다.

흐름(커밋마다):
  1. 공유 백본(commits/files/commit_files)에 upsert (티켓이 메시지에 있으면 기존 경로도 확보)
  2. build_retrieval_query() 로 조회 텍스트 구성  ← 매칭 품질의 핵심(사용자 구현 지점)
  3. retrieve_passages() 로 KB 조회 → 임계 점수 이상 + documentId 보유 단락을 링크로 생성
  4. (document_id, commit_id, link_type='commit') 부분 유니크 인덱스로 재실행 시 중복 방지

KB 미설정 시 2~4 단계는 빈 결과 → 백본 적재만 수행(매칭 0건이 정상).
"""

import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import git
from app.core.config import get_trace_backfill_min_confidence
from app.core.knowledge_base import Passage, retrieve_passages
from app.core.tickets import extract_ticket
from app.db import crud_common
from app.db.models import DocumentLink
from app.features.blame.service import extract_keywords
from app.features.timeline.crud import _parse_date

logger = logging.getLogger(__name__)


def build_retrieval_query(commit: dict) -> str:
    """커밋 한 건에서 KB 시맨틱 조회에 쓸 텍스트를 만든다.

    이 함수가 백필 매칭 품질을 좌우한다 — 어떤 신호를 쿼리에 넣느냐에 따라 어느 기획 단락이
    걸려오는지가 달라진다. commit 형식:
        {"hash","author","date","subject","files":[{"path","added","removed"}, ...]}

    고려할 트레이드오프:
      - 커밋 메시지 키워드: extract_keywords(commit["subject"]) 가 불용어 제거 + 도메인 용어
        우선정렬을 이미 해 준다(blame 과 동일 규칙).
      - 변경 파일명: 경로보다 파일명/식별자(camelCase·snake_case 분해)가 도메인 신호가 강하다.
        단, 변경 파일이 많은 커밋은 노이즈가 커지므로 상위 몇 개로 제한하는 편이 낫다.
      - 너무 길면 임베딩이 희석되고, 너무 짧으면 회수율이 떨어진다.

    TODO(당신): 위 신호들을 조합해 한 줄 쿼리 문자열을 반환하세요(5~10줄).
    아래는 메시지 키워드만 쓰는 baseline 입니다 — 파일명 신호를 더해 품질을 높여 보세요.
    """
    return " ".join(extract_keywords(commit.get("subject", "")))


async def run_backfill(
    db: AsyncSession,
    repo_path: str,
    *,
    since: str = "",
    limit: int = 0,
    min_confidence: float | None = None,
) -> dict:
    """레포 전체 히스토리를 백필한다. 반환: BackfillResponse 필드 dict."""
    threshold = min_confidence if min_confidence is not None else get_trace_backfill_min_confidence()
    repo = await crud_common.get_or_create_repository(db, repo_path)
    log = git.get_repo_log(repo_path, since=since, limit=limit or 0)

    scanned = matched = links = 0
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

        query = build_retrieval_query(entry)
        passages = retrieve_passages(query) if query.strip() else []
        made = await _link_passages(db, commit.id, passages, threshold)
        if made:
            matched += 1
            links += made

    await db.commit()
    return {
        "commitsScanned": scanned,
        "commitsMatched": matched,
        "linksCreated": links,
    }


async def _link_passages(
    db: AsyncSession, commit_id: int, passages: list[Passage], threshold: float
) -> int:
    """임계 점수 이상이고 documentId 가 있는 단락을 commit 링크로 만든다(중복은 skip).

    같은 (document_id, commit_id, link_type='commit') 는 부분 유니크 인덱스로 막혀 있어
    on_conflict_do_nothing 으로 재실행에도 안전하다. 반환: 실제 생성된 링크 수.
    """
    # TODO(검증): Bedrock KB 의 score 가 0~1 정규화 값인지 확인 후 threshold(기본 0.4) 보정.
    #   임베딩 모델/검색 방식에 따라 score 스케일이 달라질 수 있어, 실데이터로 분포를 보고 조정한다.
    # TODO(선택): 점수가 애매한 구간(threshold 근처)은 Bedrock 으로 "이 변경이 충족하는 요구사항"을
    #   확인/요약해 excerpt 를 다듬는 LLM 보강 단계 추가(비용 통제 위해 임계 구간에만). 현재는 미구현.
    made = 0
    for p in passages:
        if p.document_id is None or (p.score is not None and p.score < threshold):
            continue
        stmt = (
            pg_insert(DocumentLink)
            .values(
                document_id=p.document_id,
                link_type="commit",
                commit_id=commit_id,
                page=p.page,
                section=p.section,
                excerpt=p.text[:500] if p.text else None,
                confidence=p.score,
            )
            .on_conflict_do_nothing(
                index_elements=["document_id", "commit_id", "link_type"],
                index_where=DocumentLink.commit_id.isnot(None),
            )
        )
        result = await db.execute(stmt)
        # TODO(검증): asyncpg + on_conflict_do_nothing 에서 rowcount 가 삽입 1 / 스킵 0 으로
        #   정확히 오는지 확인. 부정확하면 RETURNING id 로 실제 삽입 여부를 판정하도록 바꾼다.
        made += result.rowcount or 0
    return made
