from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres import get_db
from app.features.documents import service
from app.features.documents.schemas import DocumentSearchItem, DocumentSearchRequest

router = APIRouter()


@router.post("/search", response_model=list[DocumentSearchItem])
async def search_documents(body: DocumentSearchRequest, db: AsyncSession = Depends(get_db)):
    """커밋 메시지 키워드로 연관 문서 검색. 없으면 최신 문서 1건 반환."""
    rows = await service.search_by_keywords(db, body.keywords)
    return [
        DocumentSearchItem(
            id=r["id"],
            name=r["name"],
            comment=r["comment"],
            downloadUrl=f"/api/documents/{r['id']}/download",
            pageCount=r["pageCount"],
        )
        for r in rows
    ]


@router.get("/{document_id}/download")
async def download_document(document_id: int, db: AsyncSession = Depends(get_db)):
    result = await service.get_file_data(db, document_id)
    if result is None:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    data, content_type, name = result
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
