"""Requirement Trace 요청/응답 모델.

프론트엔드 src/shared/types.ts 의 TraceRequest / TraceResult 와 키 이름이 일치해야 한다.

👤 담당: 개발자 C
"""

from pydantic import BaseModel


class TraceRequest(BaseModel):
    filePath: str
    line: int
    repoPath: str


class DocumentMatch(BaseModel):
    documentId: int
    name: str                  # 업로드 원본 파일명
    page: int | None = None
    excerpt: str | None = None
    downloadUrl: str           # /api/documents/{id}/download


class TraceResponse(BaseModel):
    documents: list[DocumentMatch]
