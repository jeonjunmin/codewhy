"""Context Blame 요청/응답 모델.

프론트엔드 src/shared/types.ts 의 BlameRequest / BlameResult 와 키 이름이 일치해야 한다.

👤 담당: 개발자 A
"""

from pydantic import BaseModel


class BlameRequest(BaseModel):
    filePath: str
    line: int
    repoPath: str


class BlameResponse(BaseModel):
    explanation: str
    commitHash: str
    author: str
    date: str
