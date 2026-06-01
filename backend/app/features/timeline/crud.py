"""Timeline — DynamoDB CRUD.

② 흐름: EC2가 DynamoDB에서 커밋 이력을 읽어오는 레이어.

테이블: codewhy_commit_logs (스키마 상세 → app/db/dynamo_schema.py)
  PK: project_id  = "{repo_path}#{file_path}"
  SK: commit_sk   = "{YYYY-MM-DD}#{commit_hash[:8]}"

주요 연산:
  upsert_commits — 신규 커밋을 batch_writer 로 put_item (중복은 덮어씀)
  get_commits    — project_id 로 Query, 최신순 반환
"""

from datetime import datetime, timezone

from boto3.dynamodb.conditions import Key

from app.core.config import get_settings
from app.db.dynamo_session import get_resource_kwargs, get_session


def _make_project_id(repo_path: str, file_path: str) -> str:
    return f"{repo_path}#{file_path}"


def _make_commit_sk(date: str, commit_hash: str) -> str:
    """SK = "{YYYY-MM-DD}#{hash[:8]}" — 날짜 오름차순 정렬 보장."""
    return f"{date}#{commit_hash[:8]}"


async def upsert_commits(
    repo_path: str, file_path: str, commits: list[dict]
) -> None:
    """커밋 목록을 DynamoDB 에 저장한다. 같은 SK 는 덮어쓴다 (upsert 효과)."""
    if not commits:
        return

    project_id = _make_project_id(repo_path, file_path)
    now = datetime.now(timezone.utc).isoformat()
    table_name = get_settings().DYNAMODB_COMMIT_TABLE

    async with get_session().resource("dynamodb", **get_resource_kwargs()) as dynamo:
        table = await dynamo.Table(table_name)
        async with table.batch_writer() as batch:
            for c in commits:
                await batch.put_item(Item={
                    "project_id":  project_id,
                    "commit_sk":   _make_commit_sk(c["date"], c["hash"]),
                    "commit_hash": c["hash"],
                    "author":      c["author"],
                    "message":     c["subject"],
                    "created_at":  now,
                })


async def get_commits(
    repo_path: str, file_path: str, limit: int = 200
) -> list[dict]:
    """파일의 커밋 이력을 최신순으로 반환한다.

    graph.py 가 기대하는 형식:
      [{"hash": str, "author": str, "date": str, "subject": str}, ...]
    """
    project_id = _make_project_id(repo_path, file_path)
    table_name = get_settings().DYNAMODB_COMMIT_TABLE

    async with get_session().resource("dynamodb", **get_resource_kwargs()) as dynamo:
        table = await dynamo.Table(table_name)
        resp = await table.query(
            KeyConditionExpression=Key("project_id").eq(project_id),
            ScanIndexForward=False,   # SK 내림차순 → 최신 커밋 먼저
            Limit=limit,
        )

    return [
        {
            "hash":    item["commit_hash"],
            "author":  item["author"],
            "date":    item["commit_sk"].split("#")[0],   # SK 에서 날짜 복원
            "subject": item["message"],
        }
        for item in resp.get("Items", [])
    ]


async def get_commits_by_author(author: str, limit: int = 100) -> list[dict]:
    """유저별 전체 커밋 조회 — GSI(author-date-index) 사용."""
    table_name = get_settings().DYNAMODB_COMMIT_TABLE

    async with get_session().resource("dynamodb", **get_resource_kwargs()) as dynamo:
        table = await dynamo.Table(table_name)
        resp = await table.query(
            IndexName="author-date-index",
            KeyConditionExpression=Key("author").eq(author),
            ScanIndexForward=False,
            Limit=limit,
        )

    return [
        {
            "hash":       item["commit_hash"],
            "author":     item["author"],
            "date":       item["commit_sk"].split("#")[0],
            "subject":    item["message"],
            "project_id": item["project_id"],
        }
        for item in resp.get("Items", [])
    ]
