"""LLM 클라이언트 래퍼.

각 기능의 service.py 가 자기 도메인에 맞는 프롬프트를 만들어 호출한다.
프롬프트 자체는 각 기능 폴더에서 관리하므로 이 파일은 모델/토큰 설정만 담당한다.

두 경로를 지원한다:
 - call_claude  : Anthropic 직접 API (타임라인 요약 등 기존 기능)
 - call_bedrock : AWS Bedrock Converse API (컨텍스트 블레임 RAG)
"""

import anthropic
import boto3
from app.core.config import (
    get_anthropic_api_key,
    get_aws_region,
    get_bedrock_model_id,
)

_client: anthropic.Anthropic | None = None
_bedrock_runtime = None

DEFAULT_MODEL = "claude-opus-4-7"


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=get_anthropic_api_key())
    return _client


def call_claude(prompt: str, *, max_tokens: int = 600, model: str = DEFAULT_MODEL) -> str:
    """단발성 프롬프트를 Anthropic 직접 API 로 보내고 텍스트 응답을 반환한다."""
    message = _get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _get_bedrock_runtime():
    global _bedrock_runtime
    if _bedrock_runtime is None:
        _bedrock_runtime = boto3.client("bedrock-runtime", region_name=get_aws_region())
    return _bedrock_runtime


def call_bedrock(
    prompt: str,
    *,
    system: str | None = None,
    max_tokens: int = 600,
    temperature: float = 0.2,
    model_id: str | None = None,
) -> str:
    """프롬프트를 AWS Bedrock Converse API 로 보내고 텍스트 응답을 반환한다.

    Converse API 는 모델별 JSON 바디 차이를 흡수하므로, 모델 교체 시
    BEDROCK_MODEL_ID 환경변수만 바꾸면 된다.
    """
    kwargs: dict = {
        "modelId": model_id or get_bedrock_model_id(),
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
    }
    if system:
        kwargs["system"] = [{"text": system}]

    response = _get_bedrock_runtime().converse(**kwargs)
    return response["output"]["message"]["content"][0]["text"]
