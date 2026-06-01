"""DynamoDB 테이블 스키마 설계 및 생성 유틸리티.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
테이블: codewhy_commit_logs
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

키 설계:
  Partition Key (PK)  project_id  String
    → "{repo_path}#{file_path}"
    → 같은 파일의 모든 커밋을 한 파티션에 집중 → 파일별 Query 최적화

  Sort Key (SK)       commit_sk   String
    → "{YYYY-MM-DD}#{commit_hash[:8]}"
    → 날짜 오름차순 정렬 (lexicographic)
    → ScanIndexForward=False 시 최신순 반환
    → begins_with / between 으로 날짜 범위 쿼리 가능

속성:
  commit_hash  (String)  전체 40자 해시
  author       (String)  커밋 작성자
  message      (String)  커밋 메시지
  created_at   (String)  DynamoDB 저장 시각 (ISO-8601)

GSI — author-date-index  (유저별/날짜별 조회)
  PK: author    (String)   작성자명
  SK: commit_sk (String)   날짜순 정렬 상속

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
예시 아이템
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{
  "project_id":  "/repos/codewhy#src/features/timeline/service.py",
  "commit_sk":   "2024-03-15#a1b2c3d4",
  "commit_hash": "a1b2c3d4e5f6...",
  "author":      "박성태",
  "message":     "feat[timeline]: LangGraph 파이프라인 추가",
  "created_at":  "2025-06-01T12:00:00Z"
}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
쿼리 패턴
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 파일의 전체 이력 (최신순)
  KeyConditionExpression = Key("project_id").eq(project_id)
  ScanIndexForward = False

# 파일의 특정 기간 이력
  KeyConditionExpression = (
      Key("project_id").eq(project_id) &
      Key("commit_sk").between("2024-01-01", "2024-12-31")
  )

# 특정 유저의 전체 커밋 (GSI)
  IndexName = "author-date-index"
  KeyConditionExpression = Key("author").eq("박성태")
"""

import boto3
from datetime import timezone, datetime

from app.db.dynamo_session import get_client_kwargs
from app.core.config import get_settings


def create_commit_logs_table() -> None:
    """로컬 개발 또는 초기 셋업 시 테이블과 GSI를 생성한다.

    이미 테이블이 존재하면 오류 없이 건너뛴다.
    운영 환경에서는 AWS Console / CDK / Terraform 으로 관리하는 것을 권장한다.
    """
    client = boto3.client("dynamodb", **get_client_kwargs())
    table_name = get_settings().DYNAMODB_COMMIT_TABLE

    try:
        client.create_table(
            TableName=table_name,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "project_id", "AttributeType": "S"},
                {"AttributeName": "commit_sk",  "AttributeType": "S"},
                {"AttributeName": "author",     "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "project_id", "KeyType": "HASH"},
                {"AttributeName": "commit_sk",  "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "author-date-index",
                    "KeySchema": [
                        {"AttributeName": "author",    "KeyType": "HASH"},
                        {"AttributeName": "commit_sk", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        client.get_waiter("table_exists").wait(TableName=table_name)
    except client.exceptions.ResourceInUseException:
        pass   # 이미 존재 → 정상
