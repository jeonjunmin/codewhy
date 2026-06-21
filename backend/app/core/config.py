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

# config.py 기준 상위 3단계(app/core → app → backend)의 .env 를 절대경로로 참조.
# cwd 에 무관하게 항상 backend/.env 를 읽는다.
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"

# boto3 는 os.environ 을 직접 읽으므로, pydantic-settings 가 읽기 전에
# load_dotenv() 로 .env → os.environ 에 먼저 주입해야 한다.
load_dotenv(_ENV_FILE, override=True)


class Settings(BaseSettings):
    # ── AWS 공통 ──────────────────────────────────────────────────────────────
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_SESSION_TOKEN: str = ""
    AWS_DEFAULT_REGION: str = "ap-northeast-2"

    # ── AWS Bedrock ───────────────────────────────────────────────────────────
    BEDROCK_MODEL_ID: str = "global.anthropic.claude-sonnet-4-6"

    # ── PostgreSQL (RDS) ──────────────────────────────────────────────────────
    # 런타임(asyncpg): postgresql+asyncpg://user:pass@host:5432/codewhy
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/codewhy"

    # ── 기타 ──────────────────────────────────────────────────────────────────
    # 업로드된 기획 문서 바이너리를 보관할 서버 디렉터리 (역추적 다운로드용)
    DOCUMENTS_DIR: str = "./uploaded_documents"

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """앱 전역 싱글턴. 최초 호출 시 .env 를 한 번만 파싱한다."""
    return Settings()


# ── DB URL 헬퍼 ───────────────────────────────────────────────────────────────

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
    """Alembic 마이그레이션용 동기(psycopg2) 접속 URL."""
    return _with_driver(get_settings().DATABASE_URL, "psycopg2")


def get_database_url() -> str:
    """asyncpg 비동기 URL (FastAPI 앱용)."""
    return get_rds_url_async()


# ── 하위 호환 헬퍼 ────────────────────────────────────────────────────────────

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


# ─── AWS Bedrock ────────────────────────────────────────────────────────────

def get_bedrock_model_id() -> str:
    """Converse API 로 호출할 Bedrock 모델 ID (inference profile ID 권장)."""
    return get_settings().BEDROCK_MODEL_ID

def get_bedrock_kb_id() -> str:
    """기획서 단락을 조회할 Bedrock Knowledge Base ID. 미설정 시 RAG 생략."""
    return os.getenv("BEDROCK_KNOWLEDGE_BASE_ID", "")

def get_bedrock_kb_max_results() -> int:
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


def get_self_hosted_gitlab_hosts() -> tuple[str, ...]:
    """자체 호스팅 GitLab 호스트 화이트리스트(소문자, 정확 일치).

    vcs._parse_remote_url 은 기본적으로 gitlab.com(과 그 하위 도메인)만 GitLab 으로 인정한다.
    `git.example.com` 처럼 'gitlab' 이 들어가지 않는 사내 GitLab 을 쓰면 여기에 등록해야
    PRIVATE-TOKEN 이 그 호스트로 전송된다. 미설정이면 gitlab.com 외에는 토큰을 보내지 않는다
    (위장 도메인으로의 토큰 유출 차단).

    env: CODEWHY_GITLAB_HOSTS="git.example.com,gitlab.internal.corp"
    """
    raw = os.getenv("CODEWHY_GITLAB_HOSTS", "")
    if not raw.strip():
        return ()
    return tuple(d.strip().lower() for d in raw.split(",") if d.strip())


def get_attachment_domain_allowlist() -> tuple[str, ...]:
    """이슈 본문에서 '첨부'로 인정할 외부 도메인 목록.

    GitHub user-attachments 와 흔한 문서 확장자는 vcs.py 가 기본으로 처리한다.
    Notion·Confluence·Wiki 처럼 확장자 없는 위키 링크를 첨부로 띄우려면 여기에 도메인을 추가한다.

    env: CODEWHY_ATTACHMENT_DOMAINS="notion.so,confluence.atlassian.com,wiki.example.com"
    """
    raw = os.getenv("CODEWHY_ATTACHMENT_DOMAINS", "")
    if not raw.strip():
        return ()
    return tuple(d.strip().lower() for d in raw.split(",") if d.strip())
