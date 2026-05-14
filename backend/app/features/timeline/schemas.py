"""Timeline Summary 요청/응답 모델.

프론트엔드 src/shared/types.ts 의 TimelineRequest / TimelineResult 와 키 이름이 일치해야 한다.

👤 담당: 개발자 B
"""

from pydantic import BaseModel


class TimelineRequest(BaseModel):
    filePath: str
    repoPath: str


class Milestone(BaseModel):
    date: str
    description: str


class TimelineResponse(BaseModel):
    summary: str
    milestones: list[Milestone]
