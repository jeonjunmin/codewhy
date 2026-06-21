"""이슈 챗봇 요청 모델.

프론트엔드 src/shared/types.ts 의 IssueChatRequest / IssueChatMessage 와 키 이름이 일치해야 한다.
첨부/댓글은 프런트가 이미 가진 DocumentMatch 를 그대로 흘려보내므로 dict 로 느슨하게 받는다.

👤 담당: 이슈 챗봇
"""

from pydantic import BaseModel


class ChatMessage(BaseModel):
    """멀티턴 대화 히스토리 한 줄. 마지막 원소가 이번 질문이다."""
    role: str          # "user" | "assistant"
    content: str


class LinkedCommit(BaseModel):
    """이슈에 연관된 커밋(이미지의 '커밋 N' 칩 수준). 프런트가 활동 타임라인에서 추린다."""
    sha: str
    summary: str = ""  # 커밋 메시지 첫 줄


class IssueChatRequest(BaseModel):
    # ── 이슈 본체 ──────────────────────────────────────────────────────────
    issueNumber: int | None = None
    title: str = ""
    body: str = ""
    state: str = ""
    labels: list[str] = []
    assignee: str = ""
    url: str = ""

    # ── 첨부/활동(프런트의 DocumentMatch 그대로) ───────────────────────────
    # attachments: [{label, url, pageCount?}] — 백엔드가 url 로 바이트를 받아 멀티모달화
    attachments: list[dict] = []
    # comments: IssueComment[] — 코멘트 + 시스템 이벤트(committed/referenced 포함), 각 항목에 attachments
    comments: list[dict] = []

    # ── 연관 커밋/코드 ─────────────────────────────────────────────────────
    linkedCommits: list[LinkedCommit] = []

    # ── 현재 파일/레포 맥락(있으면 첨부 다운로드 토큰 라우팅 등에 참고) ────
    filePath: str = ""
    repoPath: str = ""
    remoteUrl: str | None = None

    # ── 대화 ───────────────────────────────────────────────────────────────
    messages: list[ChatMessage]
