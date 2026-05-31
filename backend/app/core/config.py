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


def get_aws_credentials() -> dict:
    """MFA STS 임시 자격증명. 미설정 시 빈 dict → boto3가 ~/.aws/credentials 폴백."""
    key = os.getenv("AWS_ACCESS_KEY_ID", "")
    secret = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    token = os.getenv("AWS_SESSION_TOKEN", "")
    if key and secret:
        creds = {"aws_access_key_id": key, "aws_secret_access_key": secret}
        if token:
            creds["aws_session_token"] = token
        return creds
    return {}


# ─── AWS Bedrock (Context Blame RAG) ────────────────────────────────
def get_bedrock_model_id() -> str:
    """Converse API 로 호출할 Bedrock 모델 ID (inference profile ID 권장)."""
    return os.getenv("BEDROCK_MODEL_ID", "apac.anthropic.claude-3-5-sonnet-20241022-v2:0")


def get_bedrock_kb_id() -> str:
    """기획서 단락을 조회할 Bedrock Knowledge Base ID. 미설정 시 RAG 생략."""
    return os.getenv("BEDROCK_KNOWLEDGE_BASE_ID", "")


def get_bedrock_kb_max_results() -> int:
    """Knowledge Base 한 번 조회 시 가져올 기획서 단락 수."""
    try:
        return int(os.getenv("BEDROCK_KB_MAX_RESULTS", "4"))
    except ValueError:
        return 4


def get_document_paths() -> list[str]:
    raw = os.getenv("DOCUMENT_PATHS", "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def get_dynamo_blame_table() -> str:
    return os.getenv("DYNAMO_BLAME_TABLE", "codewhy_blame_cache")


def get_dynamo_timeline_table() -> str:
    return os.getenv("DYNAMO_TIMELINE_TABLE", "codewhy_timeline_cache")
