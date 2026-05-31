"""AWS Bedrock LLM 클라이언트 — boto3 자격증명 체인 정석 패턴.

핵심 원칙: boto3 에 AWS 키를 직접 전달하지 않는다.
boto3 는 아래 순서로 자격증명을 자동 탐색한다:
  1순위 — 환경변수: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
           (pydantic-settings 가 .env → os.environ 에 주입)
  2순위 — ~/.aws/credentials 프로파일
  3순위 — EC2 IAM Instance Profile (운영 환경, 코드 변경 없음)

따라서 로컬과 EC2 모두 이 파일은 수정 없이 동작한다.
"""

import boto3
from langchain_aws import ChatBedrock

from app.core.config import get_settings


def get_bedrock_llm(max_tokens: int = 800) -> ChatBedrock:
    """boto3 자격증명 체인을 사용하는 Bedrock LLM 인스턴스를 반환한다.

    boto3.Session() 에 키를 직접 넘기지 않는 것이 포인트.
    리전은 AWS_DEFAULT_REGION 환경변수를 boto3 가 자동으로 읽는다.
    """
    # 키를 코드에 하드코딩하거나 인자로 전달하지 않는다 ← 정석
    client = boto3.client("bedrock-runtime")

    return ChatBedrock(
        model_id=get_settings().BEDROCK_MODEL_ID,
        client=client,
        model_kwargs={"max_tokens": max_tokens},
    )
