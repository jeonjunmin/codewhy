from pydantic import BaseModel


class DocumentSearchRequest(BaseModel):
    keywords: list[str]


class DocumentSearchItem(BaseModel):
    id: int
    name: str
    comment: str | None = None
    downloadUrl: str
    pageCount: int | None = None
