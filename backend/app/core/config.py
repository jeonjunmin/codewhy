"""설정 관리 — pydantic-settings 기반.

pydantic-settings 가 앱 시작 시 backend/.env 파일을 자동으로 읽어
os.environ 에 주입한다. 이후 boto3 는 별도 코드 없이 아래 표준 환경변수를
자동으로 인식한다:

    AWS_ACCESS_KEY_ID      → boto3 자격증명
    AWS_SECRET_ACCESS_KEY  → boto3 자격증명
    AWS_DEFAULT_REGION     → boto3 리전 (boto3 공식 표준 이름)

EC2 배포 시 IAM Instance Profile 을 사용하면 세 변수 모두 비워도 된다.
boto3 자격증명 체인: 환경변수 → ~/.aws/credentials → EC2 Instance Profile
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── AWS 공통 ──────────────────────────────────────────────────────────────
    # 로컬: .env 에 실제 키 입력 / EC2: 비워두면 Instance Profile 자동 사용
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_DEFAULT_REGION: str = "ap-northeast-2"   # boto3 표준 환경변수명

    # ── AWS Bedrock ───────────────────────────────────────────────────────────
    BEDROCK_MODEL_ID: str = "anthropic.claude-3-5-sonnet-20240620-v1:0"

    # ── AWS RDS (PostgreSQL) ──────────────────────────────────────────────────
    RDS_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/codewhy"
    RDS_URL_SYNC: str = "postgresql+psycopg2://postgres:password@localhost:5432/codewhy"

    # ── AWS DynamoDB ──────────────────────────────────────────────────────────
    DYNAMO_BLAME_TABLE: str = "codewhy_blame_cache"
    DYNAMO_TIMELINE_TABLE: str = "codewhy_timeline_cache"

    # ── Anthropic (blame/traceability 전용) ───────────────────────────────────
    ANTHROPIC_API_KEY: str = ""

    # ── 기타 ──────────────────────────────────────────────────────────────────
    AUTO_CREATE_TABLES: bool = False
    DOCUMENT_PATHS: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",   # .env 에 알 수 없는 키가 있어도 오류 없이 무시
    )


@lru_cache
def get_settings() -> Settings:
    """앱 전역 싱글턴 설정 인스턴스. 최초 호출 시 .env 를 파싱한다."""
    return Settings()


# ── 하위 호환 헬퍼 (기존 코드가 get_*() 형태로 import 하므로 유지) ─────────────

def get_anthropic_api_key() -> str:
    return get_settings().ANTHROPIC_API_KEY

def get_aws_region() -> str:
    return get_settings().AWS_DEFAULT_REGION

def get_document_paths() -> list[str]:
    raw = get_settings().DOCUMENT_PATHS
    return [p.strip() for p in raw.split(",") if p.strip()]

def get_dynamo_blame_table() -> str:
    return get_settings().DYNAMO_BLAME_TABLE

def get_dynamo_timeline_table() -> str:
    return get_settings().DYNAMO_TIMELINE_TABLE

def get_bedrock_model_id() -> str:
    return get_settings().BEDROCK_MODEL_ID

def get_rds_url() -> str:
    return get_settings().RDS_URL

def get_rds_url_sync() -> str:
    return get_settings().RDS_URL_SYNC

def auto_create_tables() -> bool:
    return get_settings().AUTO_CREATE_TABLES
