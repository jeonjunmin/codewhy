from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession


async def search_by_keywords(db: AsyncSession, keywords: list[str]) -> list[dict]:
    """커밋 메시지 키워드로 original_name 부분 일치 검색. 없으면 최신 문서 1건 반환."""
    if keywords:
        conditions = " OR ".join(
            f"original_name ILIKE :kw{i}" for i in range(len(keywords))
        )
        params = {f"kw{i}": f"%{kw}%" for i, kw in enumerate(keywords)}
        rows = (await db.execute(
            text(f"SELECT id, original_name, content_type, size_bytes, page_count, uploaded_at FROM documents WHERE {conditions} ORDER BY uploaded_at DESC"),
            params,
        )).fetchall()
        if rows:
            return [_to_dict(r) for r in rows]

    rows = (await db.execute(
        text("SELECT id, original_name, content_type, size_bytes, page_count, uploaded_at FROM documents ORDER BY uploaded_at DESC LIMIT 1")
    )).fetchall()
    return [_to_dict(r) for r in rows]


async def get_file_data(db: AsyncSession, document_id: int) -> tuple[bytes, str, str] | None:
    """DB에서 파일 바이너리, content_type, original_name 반환."""
    row = (await db.execute(
        text("SELECT file_data, content_type, original_name FROM documents WHERE id = :id"),
        {"id": document_id},
    )).fetchone()
    if row is None or row[0] is None:
        return None
    return row[0], row[1] or "application/octet-stream", row[2]


def _to_dict(row) -> dict:
    size_mb = f"{row.size_bytes / 1024 / 1024:.1f} MB" if row.size_bytes else None
    parts = []
    if row.content_type:
        parts.append(row.content_type.split("/")[-1].upper())
    if row.page_count:
        parts.append(f"{row.page_count}페이지")
    if size_mb:
        parts.append(size_mb)
    if row.uploaded_at:
        parts.append(row.uploaded_at.strftime("%Y-%m-%d"))
    return {
        "id": row.id,
        "name": row.original_name,
        "comment": " · ".join(parts) if parts else None,
        "pageCount": row.page_count,
    }
