import boto3
import os
from boto3.dynamodb.conditions import Key

_dynamodb = None


def get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource(
            "dynamodb",
            region_name=os.getenv("AWS_REGION", "ap-northeast-2"),
        )
    return _dynamodb


def get_table(table_name: str):
    return get_dynamodb().Table(table_name)


# ------------------------------------------------------------------
# blame_cache 테이블 헬퍼
# 파티션키: repo_path (String)
# 정렬키:   file_line  (String, "{file_path}#{line}")
# ------------------------------------------------------------------
def get_blame_cache(repo_path: str, file_path: str, line: int) -> dict | None:
    table = get_table(os.getenv("DYNAMO_BLAME_TABLE", "codewhy_blame_cache"))
    resp = table.get_item(
        Key={"repo_path": repo_path, "file_line": f"{file_path}#{line}"}
    )
    return resp.get("Item")


def put_blame_cache(repo_path: str, file_path: str, line: int, item: dict):
    table = get_table(os.getenv("DYNAMO_BLAME_TABLE", "codewhy_blame_cache"))
    table.put_item(
        Item={
            "repo_path": repo_path,
            "file_line": f"{file_path}#{line}",
            **item,
        }
    )


# ------------------------------------------------------------------
# timeline_cache 테이블 헬퍼
# 파티션키: repo_path (String)
# 정렬키:   file_path (String)
# ------------------------------------------------------------------
def get_timeline_cache(repo_path: str, file_path: str) -> dict | None:
    table = get_table(os.getenv("DYNAMO_TIMELINE_TABLE", "codewhy_timeline_cache"))
    resp = table.get_item(Key={"repo_path": repo_path, "file_path": file_path})
    return resp.get("Item")


def put_timeline_cache(repo_path: str, file_path: str, item: dict):
    table = get_table(os.getenv("DYNAMO_TIMELINE_TABLE", "codewhy_timeline_cache"))
    table.put_item(Item={"repo_path": repo_path, "file_path": file_path, **item})
