"""LLM 클라이언트 래퍼 — AWS Bedrock 전용.

각 기능의 service.py 가 자기 도메인에 맞는 프롬프트를 만들어 호출한다.
프롬프트 자체는 각 기능 폴더에서 관리하므로 이 파일은 모델/토큰 설정만 담당한다.

보안 원칙: **AI 호출은 AWS Bedrock(사내 계정 격리) 하나로만 나간다.**
외부 SaaS LLM(Anthropic 직접 API 등)으로는 코드·이력을 보내지 않는다.
스트리밍 경로는 langchain_aws.ChatBedrock(`core/bedrock.py`)을 쓴다.
"""

import boto3

from app.core.config import (
    get_aws_credentials,
    get_aws_region,
    get_bedrock_model_id,
)

_bedrock_runtime = None


def _get_bedrock_runtime():
    global _bedrock_runtime
    if _bedrock_runtime is None:
        _bedrock_runtime = boto3.client("bedrock-runtime", region_name=get_aws_region(), **get_aws_credentials())
    return _bedrock_runtime


def call_bedrock(
    prompt: str,
    *,
    system: str | None = None,
    context: str | None = None,
    cache: bool = False,
    max_tokens: int = 600,
    temperature: float = 0.2,
    model_id: str | None = None,
) -> str:
    """프롬프트를 AWS Bedrock Converse API 로 보내고 텍스트 응답을 반환한다.

    Converse API 는 모델별 JSON 바디 차이를 흡수하므로, 모델 교체 시
    BEDROCK_MODEL_ID 환경변수만 바꾸면 된다.

    프롬프트 캐싱: `context`(여러 호출이 공유하는 긴 프리픽스)와 `cache=True` 를 주면
    content 를 [context, cachePoint, prompt] 로 구성한다. 같은 context 로 연달아 호출하면
    두 번째부터 프리픽스가 캐시 적중되어 입력 토큰 비용이 준다. 단, 모델별 최소 캐시 토큰
    임계값을 넘는 context 일 때만 실제 절감이 생기며, 작으면 무동작(무해)이다.
    """
    if context is not None:
        content: list[dict] = [{"text": context}]
        if cache:
            content.append({"cachePoint": {"type": "default"}})
        content.append({"text": prompt})
    else:
        content = [{"text": prompt}]

    kwargs: dict = {
        "modelId": model_id or get_bedrock_model_id(),
        "messages": [{"role": "user", "content": content}],
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
    }
    if system:
        kwargs["system"] = [{"text": system}]

    response = _get_bedrock_runtime().converse(**kwargs)
    return response["output"]["message"]["content"][0]["text"]
