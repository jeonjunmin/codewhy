"""시맨틱 인덱스 적재 — 업로드 문서를 Bedrock KB 데이터소스(S3)에 올린다.

브라운필드 온보딩에서, 티켓 메타데이터가 없는 레거시 문서 더미를 의미 기반으로 검색하려면
문서를 임베딩 인덱스에 넣어야 한다. 블레임이 이미 쓰는 Bedrock Knowledge Base 를 그대로
재사용한다 — KB 가 데이터소스(S3)의 원본 문서를 직접 파싱·청킹·임베딩하므로 백엔드는
"원본 바이너리 + 메타데이터 사이드카"만 S3 에 올리고 ingestion 을 트리거하면 된다.

핵심: 청크가 어느 Document 행에서 왔는지 되찾기 위해, S3 객체마다 `<key>.metadata.json`
사이드카에 documentId 를 심는다. retrieve 결과의 metadata 로 그 값이 되돌아오므로
(knowledge_base._extract_document_id), 다운로드 URL 을 정확히 만들 수 있다.

S3 버킷(DOC_INDEX_S3_BUCKET)이 미설정인 로컬/개발 환경에서는 모든 함수가 no-op 으로
동작해(=인덱싱 생략) 1차(티켓)·수동 링크 경로만으로도 깨지지 않는다.
"""

import json
import logging

import boto3

from app.core.config import (
    get_aws_credentials,
    get_aws_region,
    get_bedrock_kb_data_source_id,
    get_bedrock_kb_id,
    get_doc_index_bucket,
    get_doc_index_prefix,
)

logger = logging.getLogger(__name__)

_s3 = None
_agent = None


def is_enabled() -> bool:
    """시맨틱 인덱싱이 설정됐는지(=S3 데이터소스 버킷 지정). 미설정 시 인덱싱 생략."""
    return bool(get_doc_index_bucket())


def _s3_client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=get_aws_region(), **get_aws_credentials())
    return _s3


def _agent_client():
    global _agent
    if _agent is None:
        _agent = boto3.client("bedrock-agent", region_name=get_aws_region(), **get_aws_credentials())
    return _agent


def index_document(*, storage_key: str, local_path: str, document_id: int) -> bool:
    """문서 원본 + documentId 사이드카를 S3 데이터소스에 올린다.

    반환: 업로드 성공 여부. 미설정/실패 시 False (호출부는 indexed_at 을 채우지 않는다).
    """
    bucket = get_doc_index_bucket()
    if not bucket:
        return False

    key = f"{get_doc_index_prefix()}{storage_key}"
    try:
        s3 = _s3_client()
        with open(local_path, "rb") as f:
            s3.put_object(Bucket=bucket, Key=key, Body=f.read())
        # 사이드카 — retrieve 결과 metadata 로 documentId 가 되돌아오게 한다.
        # TODO(검증): 사이드카 포맷이 실제 Bedrock KB 버전과 맞는지 확인 필요.
        #   - 단순형 {"metadataAttributes": {"documentId": 123}} 과
        #     상세형 {"metadataAttributes": {"documentId": {"value": {"type":"NUMBER","numberValue":123},
        #             "includeForEmbedding": false}}} 중 어느 쪽을 요구하는지 콘솔/문서로 확인.
        #   - includeForEmbedding=false 여야 documentId 가 임베딩 텍스트에 섞이지 않음.
        sidecar = {"metadataAttributes": {"documentId": document_id}}
        s3.put_object(
            Bucket=bucket,
            Key=f"{key}.metadata.json",
            Body=json.dumps(sidecar).encode("utf-8"),
            ContentType="application/json",
        )
        return True
    except Exception:
        logger.exception("문서 인덱싱(S3 업로드) 실패 — document_id=%s", document_id)
        return False


def trigger_ingestion() -> str | None:
    """KB 데이터소스 ingestion job 을 시작한다(업로드분을 임베딩 인덱스에 반영).

    데이터소스 ID 미설정 시 None. 대량 업로드는 건마다 트리거하지 말고, 마지막에 한 번 호출한다.
    """
    kb_id = get_bedrock_kb_id()
    ds_id = get_bedrock_kb_data_source_id()
    if not kb_id or not ds_id:
        return None
    try:
        resp = _agent_client().start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)
        return resp.get("ingestionJob", {}).get("ingestionJobId")
    except Exception:
        logger.exception("KB ingestion job 트리거 실패")
        return None
