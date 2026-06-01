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
    # "이름표 대신 사유서" 카드의 칩/AI 추론용 — 백엔드 점진 도입을 위해 옵셔널
    ticket: str | None = None        # 예: "PAY-2041"
    specRef: str | None = None       # 예: "기획서 §4.2"
    team: str | None = None          # 예: "결제팀"
    aiSuggestion: str | None = None  # AI 개선 제안 한 문장
