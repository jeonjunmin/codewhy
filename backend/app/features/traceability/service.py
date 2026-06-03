"""Requirement Trace 비즈니스 로직.

코드 라인 → blamed 커밋 → 커밋의 티켓 → 그 티켓에 연결된 업로드 문서로 역추적한다.
문서는 서버에 보관되며, 결과에는 다운로드 URL 이 포함된다.

흐름:
  1. git blame 으로 라인의 마지막 커밋과 메시지를 얻는다.
  2. 커밋 메시지/브랜치에서 티켓(예: PAY-2041)을 추출한다.
  3. document_links(link_type='ticket') 로 같은 티켓에 연결된 문서를 조회한다.

👤 담당: 개발자 C
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import git
from app.core.tickets import extract_ticket
from app.db.models import Document, DocumentLink


async def trace(db: AsyncSession, repo_path: str, file_path: str, line: int) -> list[dict]:
    """라인을 기준으로 연관 기획 문서 목록(다운로드 URL 포함)을 반환한다."""
    try:
        info = git.get_blame_info(repo_path, file_path, line)
        branch = git.get_current_branch(repo_path)
    except Exception:
        return []

    ticket = extract_ticket(info.message, branch)
    if not ticket:
        return []

    stmt = (
        select(Document, DocumentLink)
        .join(DocumentLink, DocumentLink.document_id == Document.id)
        .where(DocumentLink.link_type == "ticket", DocumentLink.ticket == ticket)
    )
    rows = (await db.execute(stmt)).all()

    return [
        {
            "documentId": doc.id,
            "name": doc.original_name,
            "page": link.page,
            "excerpt": link.excerpt,
            "downloadUrl": f"/api/documents/{doc.id}/download",
        }
        for doc, link in rows
    ]
