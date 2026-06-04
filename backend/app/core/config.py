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

    # ── PostgreSQL (RDS) ──────────────────────────────────────────────────────
    # 런타임(asyncpg): postgresql+asyncpg://user:pass@host:5432/codewhy
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/codewhy"

    # ── Anthropic ─────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""

    # ── 기타 ──────────────────────────────────────────────────────────────────
    # 업로드된 기획 문서 바이너리를 보관할 서버 디렉터리 (역추적 다운로드용)
    DOCUMENTS_DIR: str = "./uploaded_documents"

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
def get_bedrock_kb_id() -> str:
    """기획서 단락을 조회할 Bedrock Knowledge Base ID. 미설정 시 RAG 생략."""
    return os.getenv("BEDROCK_KNOWLEDGE_BASE_ID", "")


def get_bedrock_kb_max_results() -> int:
    """Knowledge Base 한 번 조회 시 가져올 기획서 단락 수."""
    try:
        return int(os.getenv("BEDROCK_KB_MAX_RESULTS", "4"))
    except ValueError:
        return 4


# ─── 브라운필드 온보딩: 문서 인덱싱 + 커밋 백필 ──────────────────────
def get_doc_index_bucket() -> str:
    """KB 데이터소스가 읽는 S3 버킷. 미설정 시 시맨틱 인덱싱 생략(=no-op)."""
    return os.getenv("DOC_INDEX_S3_BUCKET", "")


def get_doc_index_prefix() -> str:
    """인덱싱 문서를 올릴 S3 key prefix."""
    return os.getenv("DOC_INDEX_S3_PREFIX", "codewhy-docs/")


def get_bedrock_kb_data_source_id() -> str:
    """ingestion job 을 트리거할 KB 데이터소스 ID. 미설정 시 자동 ingestion 생략."""
    return os.getenv("BEDROCK_KB_DATA_SOURCE_ID", "")


def get_trace_backfill_min_confidence() -> float:
    """커밋↔문서 백필 시 링크를 생성할 최소 시맨틱 점수(0~1)."""
    try:
        return float(os.getenv("TRACE_BACKFILL_MIN_CONFIDENCE", "0.4"))
    except ValueError:
        return 0.4


def get_documents_dir() -> str:
    """업로드된 기획 문서 바이너리를 저장/조회할 서버 디렉터리."""
    return get_settings().DOCUMENTS_DIR


def _with_driver(url: str, driver: str) -> str:
    """DATABASE_URL 의 드라이버를 강제로 교체한다.

    .env 에 `postgresql://`, `postgresql+psycopg2://`, `postgresql+asyncpg://` 중 무엇이 와도
    런타임(asyncpg)과 alembic(psycopg2)이 각자 필요한 드라이버로 안전하게 접속하도록 정규화한다.
    """
    scheme, _, rest = url.partition("://")
    base = scheme.split("+", 1)[0]
    return f"{base}+{driver}://{rest}"


def get_rds_url_async() -> str:
    """런타임(FastAPI)용 비동기(asyncpg) 접속 URL."""
    return _with_driver(get_settings().DATABASE_URL, "asyncpg")


def get_rds_url_sync() -> str:
    """Alembic 마이그레이션용 동기(psycopg2) 접속 URL.

    접속 정보를 한 곳(.env DATABASE_URL)에서만 관리하기 위해 드라이버만 psycopg2 로 바꾼다.
    """
    return _with_driver(get_settings().DATABASE_URL, "psycopg2")


# ─── Context Blame: 팀 매핑 / VCS 연동 ──────────────────────────────
def get_team_map() -> dict[str, str]:
    """작성자(이름 또는 이메일) → 팀명 매핑.

    CODEWHY_TEAM_MAP 가 가리키는 JSON 파일을 읽는다. 미설정·파일 없음·파싱 실패 시
    빈 dict 를 돌려주어(=team 칸 생략) 기능이 깨지지 않게 한다.
    """
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
    """GitHub PR 조회용 토큰. 미설정 시 PR 연동 생략."""
    return os.getenv("GITHUB_TOKEN", "")


def get_gitlab_token() -> str:
    """GitLab MR 조회용 토큰. 미설정 시 MR 연동 생략."""
    return os.getenv("GITLAB_TOKEN", "")


def get_bedrock_model_id() -> str:
    """Converse API 로 호출할 Bedrock 모델 ID (inference profile ID 권장)."""
    return get_settings().BEDROCK_MODEL_ID
