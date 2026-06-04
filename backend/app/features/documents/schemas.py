"""문서 업로드/다운로드 요청·응답 모델.

👤 담당: 개발자 C
"""

from pydantic import BaseModel


class DocumentUploadResponse(BaseModel):
    id: int
    name: str                  # original_name
    downloadUrl: str           # /api/documents/{id}/download
    pageCount: int | None = None
    linkedTickets: list[str] = []


class DocumentSearchRequest(BaseModel):
    keywords: list[str]   # 커밋 메시지에서 추출한 단어 목록


class DocumentSearchItem(BaseModel):
    id: int
    name: str
    downloadUrl: str
    pageCount: int | None = None


class BulkUploadResponse(BaseModel):
    uploaded: int                       # 저장에 성공한 문서 수
    indexed: int                        # 시맨틱 인덱스에 적재한 문서 수
    ingestionJobId: str | None = None   # KB ingestion job ID (미설정 시 None)
    documents: list[DocumentUploadResponse] = []
