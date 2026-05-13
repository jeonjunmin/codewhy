from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import git_service, ai_service
from app.db import dynamodb

router = APIRouter()


class BlameRequest(BaseModel):
    filePath: str
    line: int
    repoPath: str


class BlameResponse(BaseModel):
    explanation: str
    commitHash: str
    author: str
    date: str


@router.post("/context", response_model=BlameResponse)
def context_blame(req: BlameRequest):
    cached = dynamodb.get_blame_cache(req.repoPath, req.filePath, req.line)
    if cached:
        return BlameResponse(**cached)

    try:
        info = git_service.get_blame_info(req.repoPath, req.filePath, req.line)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"git blame 실패: {e}")

    explanation = ai_service.explain_blame(info.author, info.date, info.message, info.diff)

    result = BlameResponse(
        explanation=explanation,
        commitHash=info.commit_hash,
        author=info.author,
        date=info.date,
    )

    dynamodb.put_blame_cache(req.repoPath, req.filePath, req.line, result.model_dump())
    return result
