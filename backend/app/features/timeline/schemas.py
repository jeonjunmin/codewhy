"""Timeline Summary 요청/응답 모델.

프론트엔드 src/shared/types.ts 의 TimelineRequest / TimelineResult 와 키 이름이 일치해야 한다.

👤 담당: 개발자 B
"""

from pydantic import BaseModel


class CommitInput(BaseModel):
    """확장이 로컬 git log 에서 수집해 전송하는 커밋 한 건."""
    hash: str
    author: str
    date: str      # YYYY-MM-DD
    subject: str


class TimelineRequest(BaseModel):
    filePath: str
    repoPath: str
    commits: list[CommitInput]   # 확장이 로컬 git log 를 수집해서 보냄


class Milestone(BaseModel):
    date: str
    description: str


class TimelineResponse(BaseModel):
    summary: str
    milestones: list[Milestone]
