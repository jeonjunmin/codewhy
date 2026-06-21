"""LLM 클라이언트 래퍼 — AWS Bedrock 전용.

각 기능의 service.py 가 자기 도메인에 맞는 프롬프트를 만들어 호출한다.
프롬프트 자체는 각 기능 폴더에서 관리하므로 이 파일은 모델/토큰 설정만 담당한다.

보안 원칙: **AI 호출은 AWS Bedrock(사내 계정 격리) 하나로만 나간다.**
외부 SaaS LLM(Anthropic 직접 API 등)으로는 코드·이력을 보내지 않는다.
스트리밍 경로는 langchain_aws.ChatBedrock(`core/bedrock.py`)을 쓴다.
"""

import asyncio
import threading

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


async def stream_bedrock(
    messages: list[dict],
    *,
    system: list[dict] | str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    model_id: str | None = None,
):
    """미리 구성한 Converse 메시지(멀티모달 블록 포함)를 스트리밍으로 보내고 텍스트 델타를 흘린다.

    `call_bedrock` 은 텍스트 단발 응답용이라, image/document/cachePoint 블록과 멀티턴
    히스토리를 그대로 제어해야 하는 챗봇은 `converse_stream` 을 직접 쓴다.
    boto3 converse_stream 은 동기 블로킹 이터레이터라, 워커 스레드에서 돌리고
    이벤트 루프로 안전하게 델타를 넘긴다(이벤트 루프 블로킹 방지).

    messages: [{"role": "user"|"assistant", "content": [블록…]}, …]
    """
    kwargs: dict = {
        "modelId": model_id or get_bedrock_model_id(),
        "messages": messages,
        "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
    }
    if system:
        kwargs["system"] = system if isinstance(system, list) else [{"text": system}]

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    _DONE = object()

    def _worker():
        try:
            response = _get_bedrock_runtime().converse_stream(**kwargs)
            for event in response["stream"]:
                text = event.get("contentBlockDelta", {}).get("delta", {}).get("text")
                if text:
                    loop.call_soon_threadsafe(queue.put_nowait, text)
        except BaseException as exc:  # noqa: BLE001 — 호출 측으로 그대로 전달
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _DONE)

    threading.Thread(target=_worker, daemon=True).start()

    while True:
        item = await queue.get()
        if item is _DONE:
            return
        if isinstance(item, BaseException):
            raise item
        yield item
