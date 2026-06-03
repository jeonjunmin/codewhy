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
