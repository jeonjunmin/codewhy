"""DynamoDB 헬퍼 — blame/traceability 결과 캐시.

blame_cache     테이블: PK=repo_path, SK=file_line("{file_path}#{line}")
timeline_cache  테이블: PK=repo_path, SK=file_path
"""

import boto3
from boto3.dynamodb.conditions import Key

from app.core.config import get_settings
from app.db.dynamo_session import get_resource_kwargs

_dynamodb = None


def get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", **get_resource_kwargs())
    return _dynamodb


def get_table(table_name: str):
    return get_dynamodb().Table(table_name)


# ── blame_cache ───────────────────────────────────────────────────────────────

def get_blame_cache(repo_path: str, file_path: str, line: int) -> dict | None:
    table = get_table(get_settings().DYNAMO_BLAME_TABLE)
    resp = table.get_item(
        Key={"repo_path": repo_path, "file_line": f"{file_path}#{line}"}
    )
    return resp.get("Item")


def put_blame_cache(repo_path: str, file_path: str, line: int, item: dict):
    table = get_table(get_settings().DYNAMO_BLAME_TABLE)
    table.put_item(Item={"repo_path": repo_path, "file_line": f"{file_path}#{line}", **item})


# ── timeline_cache ────────────────────────────────────────────────────────────

def get_timeline_cache(repo_path: str, file_path: str) -> dict | None:
    table = get_table(get_settings().DYNAMO_TIMELINE_TABLE)
    resp = table.get_item(Key={"repo_path": repo_path, "file_path": file_path})
    return resp.get("Item")


def put_timeline_cache(repo_path: str, file_path: str, item: dict):
    table = get_table(get_settings().DYNAMO_TIMELINE_TABLE)
    table.put_item(Item={"repo_path": repo_path, "file_path": file_path, **item})
