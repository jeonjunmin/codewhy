"""Context Blame 요청/응답 모델.

프론트엔드 src/shared/types.ts 의 BlameRequest / BlameResult 와 키 이름이 일치해야 한다.

👤 담당: 개발자 A
"""

from pydantic import BaseModel, Field


class BlameRequest(BaseModel):
    filePath: str
    line: int
    repoPath: str


class ChangeStats(BaseModel):
    """헤더 메타 테이블의 '변경' 칸 — 추가/삭제 라인 수."""
    added: int
    removed: int


class PrInfo(BaseModel):
    """같은 PR 컨텍스트 — '동일 PR 23 라인'."""
    url: str | None = None
    lines: int


class RelatedChange(BaseModel):
    """'이 변경과 함께 일어난 일' 리스트의 한 행.

    프론트 src/shared/types.ts 의 RelatedChange 와 키가 일치해야 한다.
    """
    kind: str   # 'doc' | 'branch' | 'security' | 'commit'
    title: str  # 예: "Issue #12: 결제 취소 정책 변경" / "auth_service.py 변경"
    meta: str   # 예: "Issue #12" / "+78 라인 · 같은 PR"


class Attachment(BaseModel):
    """연관 이슈 본문에 첨부된 요구사항 문서 한 건."""
    label: str  # 표시명 (마크다운 라벨 또는 파일명)
    url: str    # 다운로드/열람 URL


class LineHistoryEntry(BaseModel):
    """사이드바 '라인 수정 이력' 한 행 — 이 라인이 실제로 바뀐 커밋 하나.

    프론트 src/shared/types.ts 의 LineHistoryEntry 와 키가 일치해야 한다.
    """
    hash: str          # 전체 커밋 해시 (프론트에서 7자리로 축약 표시)
    author: str        # 작성자
    date: str          # YYYY-MM-DD
    subject: str       # 커밋 제목 한 줄
    issueCount: int = 0  # 이 커밋이 참조하는 이슈 수 ('이슈 N' 배지)


class BlameResponse(BaseModel):
    explanation: str
    commitHash: str
    author: str
    date: str
    # explanation 이 Bedrock 추론이 아니라 폴백(호출 실패 등)인지 여부.
    # True 면 프론트가 이 결과를 캐싱하지 않아 다음 시도에 자동 재호출된다.
    aiDegraded: bool = False
    # "이름표 대신 사유서" 카드의 칩/AI 추론용 — 백엔드 점진 도입을 위해 옵셔널
    ticket: str | None = None        # 예: "PAY-2041"
    specRef: str | None = None       # 예: "Issue #12: 결제 취소 정책 변경"
    team: str | None = None          # 예: "결제팀"
    aiSuggestion: str | None = None  # AI 개선 제안 한 문장

    # ── Context Blame 사이드바 추가 필드 (모두 옵셔널, 점진 도입) ──────────
    sourceRef: str | None = None              # 예: "Issue #12: 결제 취소 정책 변경"
    issueUrl: str | None = None               # 사이드바 '출처' 클릭 시 외부로 열 URL
    attachments: list[Attachment] = Field(default_factory=list)
    changeStats: ChangeStats | None = None
    prInfo: PrInfo | None = None
    relatedChanges: list[RelatedChange] = Field(default_factory=list)
    lineHistory: list[LineHistoryEntry] = Field(default_factory=list)


class AskRequest(BaseModel):
    """AI에게 더 묻기 — 현재 라인 블레임 맥락 위에서 후속 질문."""
    filePath: str
    line: int
    repoPath: str
    question: str


class AskResponse(BaseModel):
    answer: str
