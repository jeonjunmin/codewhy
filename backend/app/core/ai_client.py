"""Anthropic Claude 클라이언트 래퍼.

각 기능의 service.py 가 자기 도메인에 맞는 프롬프트를 만들어 `call_claude` 로 호출한다.
프롬프트 자체는 각 기능 폴더에서 관리하므로 이 파일은 모델/토큰 설정만 담당한다.
"""

import anthropic
from app.core.config import get_anthropic_api_key

_client: anthropic.Anthropic | None = None

DEFAULT_MODEL = "claude-opus-4-7"


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=get_anthropic_api_key())
    return _client


def call_claude(prompt: str, *, max_tokens: int = 600, model: str = DEFAULT_MODEL) -> str:
    """단발성 프롬프트를 보내고 텍스트 응답을 반환한다."""
    message = _get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text
