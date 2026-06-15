"""Context Blame 요청/응답 모델.

프론트엔드 src/shared/types.ts 의 BlameRequest / BlameResult 와 키 이름이 일치해야 한다.

👤 담당: 개발자 A
"""

from pydantic import BaseModel, Field


class GitCommitMeta(BaseModel):
    """확장이 로컬 git 으로 뽑아 보내는 커밋 메타 + diff.

    백엔드가 원격(AWS)에 있으면 사용자 로컬 저장소에 접근할 수 없으므로, git 실행은
    저장소가 있는 확장에서 하고 그 결과만 받는다. core/git.py BlameInfo 와 키가 일치한다.
    """
    commitHash: str
    author: str
    date: str
    message: str
    diff: str = ""
    added: int = 0
    removed: int = 0


class CommitRef(BaseModel):
    """라인 이력·후속 커밋 한 행(확장이 git log 로 수집)."""
    hash: str
    author: str
    date: str
    subject: str


class BlameRequest(BaseModel):
    filePath: str
    line: int
    repoPath: str
    # ── 확장이 로컬 git 으로 수집해 전송 ──────────────────────────────────
    blame: GitCommitMeta | None = None
    unavailable: str | None = None        # 'uncommitted' | 'no_history' | None
    branch: str = ""
    lineHistory: list[CommitRef] = Field(default_factory=list)
    followups: list[CommitRef] = Field(default_factory=list)
    remoteUrl: str | None = None


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
    # 항목을 펼칠 때 /api/blame/reason 으로 지연 생성되는 그 커밋의 AI 변경 사유.
    # 초기 응답에는 비어 있고, 펼침 요청 시 프론트가 별도로 채운다.
    reason: str | None = None


class LineIssue(BaseModel):
    """'라인 수정 이력' 전체에서 dedup 된 연관 이슈 한 건 (이슈 롤업 칩).

    같은 #N 이 여러 커밋에 나와도 한 칩으로 합치고, 라인 관점의 상태를 함께 준다.
    프론트 src/shared/types.ts 의 LineIssue 와 키가 일치해야 한다.
    """
    number: int                # 이슈 번호 (#N 의 N)
    status: str                # 'current' | 'past' | 'reverted'
    changeCount: int = 1       # 이 이슈가 등장한 라인-이력 커밋 수
    url: str | None = None     # 해석되면 GitHub 이슈 URL (담당: 개발자 C)
    title: str | None = None   # 해석되면 이슈 제목 (담당: 개발자 C)


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
    # 라인 전체에서 dedup 된 연관 이슈 롤업(현재/과거/되돌림). 라인 스코프라 캐시하지 않고 매번 조립.
    lineIssues: list[LineIssue] = Field(default_factory=list)


class ReasonRequest(BaseModel):
    """라인 수정 이력 항목 펼침 — 임의 커밋 하나의 변경 사유를 요청한다."""
    filePath: str
    repoPath: str
    hash: str
    # ── 확장이 로컬 git 으로 수집해 전송 ──────────────────────────────────
    commit: GitCommitMeta | None = None
    branch: str = ""
    followups: list[CommitRef] = Field(default_factory=list)
    remoteUrl: str | None = None


class ReasonResponse(BaseModel):
    """펼침 응답 — 그 커밋의 AI 변경 사유 한 단락."""
    reason: str
    # explanation 이 Bedrock 추론이 아니라 폴백이면 True (프론트가 캐싱하지 않도록).
    aiDegraded: bool = False


class AskRequest(BaseModel):
    """AI에게 더 묻기 — 현재 라인 블레임 맥락 위에서 후속 질문."""
    filePath: str
    line: int
    repoPath: str
    question: str
    # ── 확장이 로컬 git 으로 수집해 전송 ──────────────────────────────────
    blame: GitCommitMeta | None = None
    unavailable: str | None = None        # 'uncommitted' | 'no_history' | None
    remoteUrl: str | None = None


class AskResponse(BaseModel):
    answer: str
