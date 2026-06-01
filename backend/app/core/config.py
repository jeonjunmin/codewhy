"""설정 관리 — pydantic-settings 기반.

pydantic-settings 가 backend/.env 를 읽어 os.environ 에 주입한다.
boto3 는 아래 표준 환경변수를 자동 인식한다:

    AWS_ACCESS_KEY_ID      → 자격증명
    AWS_SECRET_ACCESS_KEY  → 자격증명
    AWS_SESSION_TOKEN      → 임시 자격증명(STS/SSO) 사용 시 필수
    AWS_DEFAULT_REGION     → 리전

boto3 자격증명 탐색 순서: 환경변수 → ~/.aws/credentials → EC2 Instance Profile
"""

from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# boto3 는 os.environ 을 직접 읽으므로, pydantic-settings 가 읽기 전에
# load_dotenv() 로 .env → os.environ 에 먼저 주입해야 한다.
load_dotenv(override=False)


class Settings(BaseSettings):
    # ── AWS 공통 ──────────────────────────────────────────────────────────────
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_SESSION_TOKEN: str = ""          # STS/SSO 임시 자격증명 세션 토큰
    AWS_DEFAULT_REGION: str = "ap-northeast-2"

    # ── AWS Bedrock ───────────────────────────────────────────────────────────
    BEDROCK_MODEL_ID: str = "anthropic.claude-3-5-sonnet-20240620-v1:0"

    # ── AWS DynamoDB ──────────────────────────────────────────────────────────
    DYNAMODB_COMMIT_TABLE: str = "codewhy_commit_logs"
    DYNAMODB_URL: str = ""               # 로컬 Docker 엔드포인트 (운영 시 빈 값)

    DYNAMO_BLAME_TABLE: str = "codewhy_blame_cache"
    DYNAMO_TIMELINE_TABLE: str = "codewhy_timeline_cache"

    # ── Anthropic ─────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""

    # ── 기타 ──────────────────────────────────────────────────────────────────
    DOCUMENT_PATHS: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ── 하위 호환 헬퍼 ────────────────────────────────────────────────────────────

def get_anthropic_api_key() -> str:
    return get_settings().ANTHROPIC_API_KEY

def get_aws_region() -> str:
    return get_settings().AWS_DEFAULT_REGION

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
    raw = get_settings().DOCUMENT_PATHS
    return [p.strip() for p in raw.split(",") if p.strip()]

def get_dynamo_blame_table() -> str:
    return get_settings().DYNAMO_BLAME_TABLE

def get_dynamo_timeline_table() -> str:
    return get_settings().DYNAMO_TIMELINE_TABLE

def get_bedrock_model_id() -> str:
    return get_settings().BEDROCK_MODEL_ID
