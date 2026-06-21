"""이슈 챗봇 API 라우터.

POST /api/issue/chat
  현재 열린 이슈(본문·댓글·활동·첨부·연관 커밋)만을 컨텍스트로 멀티턴 질의응답을 한다.
  응답은 항상 SSE(text/event-stream): `data: {"delta": "..."}` … `data: {"done": true}`,
  오류 시 `data: {"error": "..."}`. (timeline /summary 스트리밍과 동일한 프레임 규약)

👤 담당: 이슈 챗봇
"""

import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.features.issue_chat import service
from app.features.issue_chat.schemas import IssueChatRequest

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/chat")
async def issue_chat(req: IssueChatRequest):
    return StreamingResponse(
        service.stream_chat(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
