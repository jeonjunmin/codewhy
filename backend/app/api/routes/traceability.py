import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import git_service, document_service

router = APIRouter()


class TraceRequest(BaseModel):
    filePath: str
    line: int
    repoPath: str


class DocumentMatch(BaseModel):
    path: str
    page: int
    excerpt: str


class TraceResponse(BaseModel):
    documents: list[DocumentMatch]


@router.post("/requirement", response_model=TraceResponse)
def requirement_trace(req: TraceRequest):
    document_paths_env = os.getenv("DOCUMENT_PATHS", "")
    document_paths = [p.strip() for p in document_paths_env.split(",") if p.strip()]

    if not document_paths:
        raise HTTPException(status_code=400, detail="DOCUMENT_PATHS 환경변수가 설정되지 않았습니다.")

    try:
        info = git_service.get_blame_info(req.repoPath, req.filePath, req.line)
        keyword = info.message.split("\n")[0][:50]
    except Exception:
        keyword = os.path.basename(req.filePath)

    matches = document_service.search_documents(keyword, document_paths)
    return TraceResponse(documents=[DocumentMatch(**m) for m in matches])
