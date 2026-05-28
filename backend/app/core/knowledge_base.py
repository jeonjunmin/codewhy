"""Bedrock Knowledge Base 조회 (RAG retrieve 단계).

커밋 메시지 키워드로 기획서 단락을 긁어온다.
LLM 호출(bedrock-runtime)과 달리 KB 조회는 별도 클라이언트(bedrock-agent-runtime)를 쓴다.

KB 가 설정되지 않은(로컬/개발) 환경에서는 빈 리스트를 돌려주어 RAG 없이도 동작한다.
"""

from dataclasses import dataclass

import boto3

from app.core.config import get_aws_region, get_bedrock_kb_id, get_bedrock_kb_max_results

_agent_runtime = None


@dataclass
class Passage:
    """KB 에서 가져온 기획서 단락 한 건."""
    text: str
    source: str   # 예: "2026_결제_기획서.pdf" 또는 S3 URI
    score: float | None = None


def _get_agent_runtime():
    global _agent_runtime
    if _agent_runtime is None:
        _agent_runtime = boto3.client("bedrock-agent-runtime", region_name=get_aws_region())
    return _agent_runtime


def retrieve_passages(query: str) -> list[Passage]:
    """쿼리로 KB 를 조회해 연관 기획서 단락 목록을 반환한다.

    KB ID 미설정·빈 쿼리·조회 실패 시 빈 리스트(=RAG 생략)를 돌려준다.
    """
    kb_id = get_bedrock_kb_id()
    if not kb_id or not query.strip():
        return []

    try:
        response = _get_agent_runtime().retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": get_bedrock_kb_max_results()},
            },
        )
    except Exception:
        return []

    passages: list[Passage] = []
    for item in response.get("retrievalResults", []):
        text = item.get("content", {}).get("text", "").strip()
        if not text:
            continue
        passages.append(
            Passage(
                text=text,
                source=_extract_source(item.get("location", {})),
                score=item.get("score"),
            )
        )
    return passages


def _extract_source(location: dict) -> str:
    """retrieve 결과의 location 에서 사람이 읽을 출처 이름을 뽑는다.

    S3 객체면 파일명만, 웹/그 외 소스면 URI 를 그대로 쓴다.
    """
    s3_uri = location.get("s3Location", {}).get("uri", "")
    if s3_uri:
        return s3_uri.rsplit("/", 1)[-1] or s3_uri

    web_url = location.get("webLocation", {}).get("url", "")
    if web_url:
        return web_url

    return location.get("type", "기획서")
