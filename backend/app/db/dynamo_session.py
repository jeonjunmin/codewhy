"""DynamoDB 세션 팩토리.

aioboto3 를 사용해 FastAPI async 환경과 호환되는 DynamoDB 리소스를 제공한다.
boto3 자격증명은 환경변수(AWS_ACCESS_KEY_ID 등)에서 자동으로 읽는다 — 코드에 키 없음.

DYNAMODB_ENDPOINT_URL 이 설정된 경우 로컬 Docker DynamoDB 로 연결한다:
  docker run -p 8001:8000 amazon/dynamodb-local
"""

import aioboto3

from app.core.config import get_settings


def get_resource_kwargs() -> dict:
    """aioboto3 resource() 에 전달할 연결 파라미터를 반환한다.

    - 운영(AWS): region_name 만 설정, 자격증명은 boto3 체인이 자동 처리
    - 로컬(Docker): endpoint_url 추가 설정
    """
    settings = get_settings()
    kwargs: dict = {"region_name": settings.AWS_DEFAULT_REGION}
    if settings.DYNAMODB_ENDPOINT_URL:
        kwargs["endpoint_url"] = settings.DYNAMODB_ENDPOINT_URL
    return kwargs


def get_client_kwargs() -> dict:
    """boto3.client() 전용 — startup 연결 확인에서 사용."""
    return get_resource_kwargs()


# 앱 전역 싱글턴 세션 (aioboto3.Session 은 재사용 가능)
_session = aioboto3.Session()


def get_session() -> aioboto3.Session:
    return _session
