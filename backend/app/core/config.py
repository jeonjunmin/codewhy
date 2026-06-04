"""설정 관리 — pydantic-settings 기반.

pydantic-settings 가 backend/.env 를 읽어 os.environ 에 주입한다.
boto3 는 아래 표준 환경변수를 자동 인식한다:

    AWS_ACCESS_KEY_ID      → 자격증명
    AWS_SECRET_ACCESS_KEY  → 자격증명
    AWS_SESSION_TOKEN      → 임시 자격증명(STS/SSO) 사용 시 필수
    AWS_DEFAULT_REGION     → 리전

boto3 자격증명 탐색 순서: 환경변수 → ~/.aws/credentials → EC2 Instance Profile
"""

import json
import os
from functools import lru_cache

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/.env 를 파일 위치 기준 절대 경로로 로드 — CWD 에 무관하게 동작
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ENV_PATH, override=False)


class Settings(BaseSettings):
    # ── AWS 공통 ──────────────────────────────────────────────────────────────
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_SESSION_TOKEN: str = ""
    AWS_DEFAULT_REGION: str = "ap-northeast-2"

    # ── AWS Bedrock ───────────────────────────────────────────────────────────
    BEDROCK_MODEL_ID: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"

    # ── PostgreSQL (RDS) ──────────────────────────────────────────────────────
    # asyncpg 드라이버 (FastAPI async) — .env 의 DATABASE_URL 로 주입
    DATABASE_URL: str = ""
    # psycopg2 드라이버 (alembic / 캐시 헬퍼 sync) — .env 의 DATABASE_URL_SYNC 로 주입
    DATABASE_URL_SYNC: str = ""

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
    """앱 전역 싱글턴. 최초 호출 시 .env 를 한 번만 파싱한다."""
    return Settings()


# ── DB URL 헬퍼 ───────────────────────────────────────────────────────────────

def get_database_url() -> str:
    """asyncpg 비동기 URL (FastAPI 앱용)."""
    return get_settings().DATABASE_URL

def get_database_url_sync() -> str:
    """psycopg2 동기 URL (alembic / 캐시 헬퍼용)."""
    return get_settings().DATABASE_URL_SYNC


# ── 하위 호환 헬퍼 ────────────────────────────────────────────────────────────

def get_anthropic_api_key() -> str:
    return get_settings().ANTHROPIC_API_KEY

def get_aws_region() -> str:
    return get_settings().AWS_DEFAULT_REGION

def get_aws_credentials() -> dict:
    """STS 임시 자격증명. 미설정 시 빈 dict → boto3가 ~/.aws/credentials 폴백."""
    key = os.getenv("AWS_ACCESS_KEY_ID", "")
    secret = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    token = os.getenv("AWS_SESSION_TOKEN", "")
    if key and secret:
        creds = {"aws_access_key_id": key, "aws_secret_access_key": secret}
        if token:
            creds["aws_session_token"] = token
        return creds
    return {}

def get_bedrock_model_id() -> str:
    return get_settings().BEDROCK_MODEL_ID

def get_bedrock_kb_id() -> str:
    """기획서 단락을 조회할 Bedrock Knowledge Base ID. 미설정 시 RAG 생략."""
    return os.getenv("BEDROCK_KNOWLEDGE_BASE_ID", "")

def get_bedrock_kb_max_results() -> int:
    try:
        return int(os.getenv("BEDROCK_KB_MAX_RESULTS", "4"))
    except ValueError:
        return 4

def get_document_paths() -> list[str]:
    raw = get_settings().DOCUMENT_PATHS
    return [p.strip() for p in raw.split(",") if p.strip()]

def get_team_map() -> dict[str, str]:
    """작성자 → 팀명 매핑 JSON 파일. 미설정·오류 시 빈 dict."""
    path = os.getenv("CODEWHY_TEAM_MAP", "")
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}

def get_github_token() -> str:
    return os.getenv("GITHUB_TOKEN", "")

def get_gitlab_token() -> str:
    return os.getenv("GITLAB_TOKEN", "")
