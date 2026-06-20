"""AWS Bedrock LLM 클라이언트 — boto3 자격증명 체인 정석 패턴.

boto3.Session 이 환경변수 → ~/.aws/credentials → EC2 Instance Profile 순서로
자격증명을 탐색한다. get_frozen_credentials() 로 확정된 자격증명을 ChatBedrock 에
명시적으로 주입해, ChatBedrock 자체 validator 가 os.environ 을 독립적으로 읽다가
key/secret 불일치 오류를 내는 문제를 방지한다.
"""

import boto3
from langchain_aws import ChatBedrock

from app.core.config import get_settings


def get_bedrock_llm(max_tokens: int = 800) -> ChatBedrock:
    """boto3 자격증명 체인으로 해석한 자격증명을 ChatBedrock 에 직접 전달한다."""
    session = boto3.Session()
    client = session.client("bedrock-runtime")

    # boto3 체인이 해석한 자격증명을 가져온다 (env → ~/.aws → IMDS 순).
    # ChatBedrock validator 는 os.environ 을 독자적으로 읽어 key 만 있고 secret 이
    # 없으면(또는 반대로) 오류를 낸다 — 여기서 둘 다 None 이거나 둘 다 값이 있도록
    # boto3 에서 일관된 값을 추출해 주입함으로써 이를 방지한다.
    creds = session.get_credentials()
    frozen = creds.get_frozen_credentials() if creds else None

    extra: dict = {}
    if frozen and frozen.access_key:
        extra["aws_access_key_id"] = frozen.access_key
        extra["aws_secret_access_key"] = frozen.secret_key
        if frozen.token:
            extra["aws_session_token"] = frozen.token

    return ChatBedrock(
        model_id=get_settings().BEDROCK_MODEL_ID,
        client=client,
        model_kwargs={"max_tokens": max_tokens},
        **extra,
    )
