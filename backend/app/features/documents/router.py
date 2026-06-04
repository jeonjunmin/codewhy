"""문서 업로드/다운로드 API 라우터.

POST /api/documents              — 기획 문서 업로드(multipart) + 티켓 연결
GET  /api/documents/{id}/download — 원본 파일 스트리밍 다운로드

👤 담당: 개발자 C
"""

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import doc_index
from app.db import crud_common
from app.db.postgres import get_db
from app.features.documents import service
from app.features.documents.schemas import BulkUploadResponse, DocumentUploadResponse

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


@router.post("/bulk", response_model=BulkUploadResponse)
async def bulk_upload_documents(
    files: list[UploadFile] = File(..., description="여러 기획/설계 문서를 한 번에 업로드"),
    repoPath: str | None = Form(None),
    uploadedBy: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """레거시 문서 더미를 일괄 적재한다(브라운필드 온보딩 1단계).

    각 문서를 저장(파일명에서 티켓 자동 추출) → 시맨틱 인덱스에 적재한 뒤,
    KB ingestion job 을 마지막에 한 번만 트리거한다.
    """
    repo_id = None
    if repoPath:
        repo = await crud_common.get_or_create_repository(db, repoPath)
        repo_id = repo.id

    results: list[DocumentUploadResponse] = []
    indexed = 0
    for f in files:
        data = await f.read()
        try:
            doc = await service.save_upload(
                db,
                original_name=f.filename or "document",
                content_type=f.content_type,
                data=data,
                repo_id=repo_id,
                uploaded_by=uploadedBy,
            )
        except Exception:
            logger.exception("대량 업로드 중 문서 저장 실패 — name=%s", f.filename)
            continue

        if await service.index_document(db, doc):
            indexed += 1

        results.append(
            DocumentUploadResponse(
                id=doc.id,
                name=doc.original_name,
                downloadUrl=f"/api/documents/{doc.id}/download",
                pageCount=doc.page_count,
                linkedTickets=[l.ticket for l in doc.links if l.ticket],
            )
        )

    # 업로드분을 임베딩 인덱스에 반영 (KB 미설정 시 None)
    ingestion_job_id = doc_index.trigger_ingestion() if indexed else None

    return BulkUploadResponse(
        uploaded=len(results),
        indexed=indexed,
        ingestionJobId=ingestion_job_id,
        documents=results,
    )


@router.get("/{document_id}/download")
async def download_document(document_id: int, db: AsyncSession = Depends(get_db)):
    doc = await service.get_document(db, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    if not doc.file_data:
        raise HTTPException(status_code=404, detail="문서 파일 데이터가 없습니다.")

    return Response(
        content=doc.file_data,
        media_type=doc.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{doc.original_name}"'},
    )
