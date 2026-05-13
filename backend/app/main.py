from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import blame, timeline, traceability

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

app.include_router(blame.router, prefix="/api/blame", tags=["Context Blame"])
app.include_router(timeline.router, prefix="/api/timeline", tags=["Timeline Summary"])
app.include_router(traceability.router, prefix="/api/trace", tags=["Requirement Trace"])


@app.get("/health")
def health():
    return {"status": "ok"}
