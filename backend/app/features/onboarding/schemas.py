"""브라운필드 온보딩 — 커밋 백필 요청/응답 모델."""

from pydantic import BaseModel


class BackfillRequest(BaseModel):
    repoPath: str
    since: str | None = None              # 예: "2024-01-01" — 이 날짜 이후 커밋만 백필
    limit: int | None = None              # 최근 N개 커밋만 (0/None = 전체)
    confidenceThreshold: float | None = None  # 미지정 시 TRACE_BACKFILL_MIN_CONFIDENCE


class BackfillResponse(BaseModel):
    commitsScanned: int        # 백본에 적재한 커밋 수
    commitsMatched: int        # 문서와 1건 이상 매칭된 커밋 수
    linksCreated: int          # 새로 만든 document_links(commit) 수
    indexConfigured: bool      # 시맨틱 인덱스(KB) 설정 여부 — False 면 매칭 0건이 정상
