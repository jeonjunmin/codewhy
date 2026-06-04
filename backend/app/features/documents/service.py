"""문서 저장/조회 + git 히스토리 연결 비즈니스 로직.

업로드된 기획 문서의 바이너리를 DB(documents.file_data)에 직접 저장한다.
document_links 가 문서를 git 히스토리(티켓)에 연결한다.

👤 담당: 개발자 C
"""

import io
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import doc_index
from app.core.tickets import extract_tickets
from app.db.models import Document, DocumentLink


async def save_upload(
    db: AsyncSession,
    *,
    original_name: str,
    content_type: str | None,
    data: bytes,
    repo_id: int | None = None,
    uploaded_by: str | None = None,
    tickets: list[str] | None = None,
) -> Document:
    """업로드 바이너리를 DB에 저장하고 documents + document_links 행을 만든다."""
    doc = Document(
        repo_id=repo_id,
        original_name=original_name,
        storage_key="",
        file_data=data,
        content_type=content_type,
        size_bytes=len(data),
        page_count=_pdf_page_count(data, content_type),
        uploaded_by=uploaded_by,
    )
    db.add(doc)
    await db.flush()   # doc.id 확보

    for link in build_document_links(doc, tickets or []):
        db.add(link)

    await db.commit()
    await db.refresh(doc)
    return doc


def build_document_links(document: Document, tickets: list[str]) -> list[DocumentLink]:
    """문서를 git 히스토리에 잇는 링크들을 만든다.

    연결 전략: 수동으로 받은 tickets + 파일명에서 자동 추출한 티켓을 합집합으로 삼아,
    각 티켓마다 link_type='ticket' 링크를 생성한다. 커밋도 같은 티켓을 보유하므로(commits.ticket),
    역추적은 이 티켓으로 코드↔문서를 잇는다.
    """
    found = list(dict.fromkeys([*tickets, *extract_tickets(document.original_name)]))
    return [
        DocumentLink(document_id=document.id, link_type="ticket", ticket=t)
        for t in found
    ]


async def index_document(db: AsyncSession, doc: Document) -> bool:
    """문서를 시맨틱 인덱스(KB 데이터소스)에 적재하고 indexed_at 을 기록한다.

    인덱싱이 미설정이거나 실패하면 False 를 돌려주고 indexed_at 은 그대로 둔다.
    ingestion job 트리거는 호출하지 않는다 — 대량 적재 시 마지막에 한 번만 돌리기 위함.
    """
    ok = doc_index.index_document(
        storage_key=doc.storage_key,
        local_path=storage_path(doc),
        document_id=doc.id,
    )
    if ok:
        doc.indexed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(doc)
    return ok


async def get_document(db: AsyncSession, document_id: int) -> Document | None:
    return await db.get(Document, document_id)


async def search_by_keywords(db: AsyncSession, keywords: list[str]) -> list[Document]:
    """커밋 메시지 키워드로 original_name 부분 일치 검색. 없으면 최신 문서 1건 반환."""
    if keywords:
        conditions = [Document.original_name.ilike(f"%{kw}%") for kw in keywords]
        stmt = select(Document).where(or_(*conditions)).order_by(Document.uploaded_at.desc())
        rows = (await db.execute(stmt)).scalars().all()
        if rows:
            return list(rows)

    # 매칭 없으면 최신 문서 1건
    stmt = select(Document).order_by(Document.uploaded_at.desc()).limit(1)
    doc = (await db.execute(stmt)).scalar_one_or_none()
    return [doc] if doc else []


def _pdf_page_count(data: bytes, content_type: str | None) -> int | None:
    """PDF 바이너리에서 페이지 수를 추출한다(그 외/실패 시 None)."""
    if content_type != "application/pdf":
        return None
    try:
        from pypdf import PdfReader
        return len(PdfReader(io.BytesIO(data)).pages)
    except Exception:
        return None
