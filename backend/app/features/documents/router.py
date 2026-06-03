"""문서 업로드/다운로드 API 라우터.

POST /api/documents              — 기획 문서 업로드(multipart) + 티켓 연결
GET  /api/documents/{id}/download — 원본 파일 스트리밍 다운로드

👤 담당: 개발자 C
"""

import logging
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import crud_common
from app.db.postgres import get_db
from app.features.documents import service
from app.features.documents.schemas import DocumentUploadResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    tickets: str = Form("", description="쉼표로 구분한 티켓 목록 (예: PAY-2041,KYC-12)"),
    repoPath: str | None = Form(None),
    uploadedBy: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    repo_id = None
    if repoPath:
        repo = await crud_common.get_or_create_repository(db, repoPath)
        repo_id = repo.id

    ticket_list = [t.strip() for t in tickets.split(",") if t.strip()]
    data = await file.read()

    try:
        doc = await service.save_upload(
            db,
            original_name=file.filename or "document",
            content_type=file.content_type,
            data=data,
            repo_id=repo_id,
            uploaded_by=uploadedBy,
            tickets=ticket_list,
        )
    except Exception as e:
        logger.exception("문서 업로드 실패 — name=%s", file.filename)
        raise HTTPException(status_code=500, detail=f"문서 업로드 실패: {e}")

    return DocumentUploadResponse(
        id=doc.id,
        name=doc.original_name,
        downloadUrl=f"/api/documents/{doc.id}/download",
        pageCount=doc.page_count,
        linkedTickets=[l.ticket for l in doc.links if l.ticket],
    )


@router.get("/{document_id}/download")
async def download_document(document_id: int, db: AsyncSession = Depends(get_db)):
    doc = await service.get_document(db, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

    path = service.storage_path(doc)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="문서 파일이 서버에 없습니다.")

    return FileResponse(
        path,
        filename=doc.original_name,
        media_type=doc.content_type or "application/octet-stream",
    )
