"""환경변수 / 설정값 로더.

`.env` 파일은 backend/.env.example 을 참고해 backend/.env 로 복사한 뒤 작성한다.
세 기능에서 모두 import 해 쓰므로 가능한 한 단순하게 유지한다.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def get_anthropic_api_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "")


def get_aws_region() -> str:
    return os.getenv("AWS_REGION", "ap-northeast-2")


def get_document_paths() -> list[str]:
    raw = os.getenv("DOCUMENT_PATHS", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def get_dynamo_blame_table() -> str:
    return os.getenv("DYNAMO_BLAME_TABLE", "codewhy_blame_cache")


def get_dynamo_timeline_table() -> str:
    return os.getenv("DYNAMO_TIMELINE_TABLE", "codewhy_timeline_cache")
