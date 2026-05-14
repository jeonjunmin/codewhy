"""CodeWhy 백엔드 진입점.

각 기능은 app/features/<기능명>/ 폴더 안에 캡슐화되어 있고,
여기서는 라우터만 모아 등록한다. 새 기능을 추가할 때도 이 파일만 건드리면 된다.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.features.blame.router import router as blame_router
from app.features.timeline.router import router as timeline_router
from app.features.traceability.router import router as traceability_router

app = FastAPI(
    title="CodeWhy Backend",
    description="컨텍스트 블레임 / 타임라인 요약 / 요구사항 역추적 API",
    version="0.0.1",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 👤 개발자 A
app.include_router(blame_router, prefix="/api/blame", tags=["Context Blame"])
# 👤 개발자 B
app.include_router(timeline_router, prefix="/api/timeline", tags=["Timeline Summary"])
# 👤 개발자 C
app.include_router(traceability_router, prefix="/api/trace", tags=["Requirement Trace"])


@app.get("/health")
def health():
    return {"status": "ok"}
