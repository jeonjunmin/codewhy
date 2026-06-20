"""Requirement Trace 요청/응답 모델.

프론트엔드 src/shared/types.ts 의 TraceRequest / TraceResult 와 키 이름이 일치해야 한다.

요구사항 문서 원천: GitHub Issue 첨부 파일 (별도 업로드 없이 Issue 워크플로우 활용)
matchType:
  - issue    : PR → Issue 직접 연결, 첨부 파일 있음 (확정)
  - ticket   : 커밋 메시지 티켓 번호로 Issue 매칭 (높음)
  - semantic : 커밋 메시지 키워드로 관련 Issue 검색 (추정)

👤 담당: 개발자 C
"""

from typing import Literal

from pydantic import BaseModel

from app.features.blame.schemas import GitCommitMeta
from app.features.timeline.schemas import CommitInput


class TraceRequest(BaseModel):
    filePath: str
    line: int = 0   # 파일 단위 추적으로 전환 — 로깅/하위호환용으로만 남는다.
    repoPath: str
    # ── 확장이 로컬 git 으로 수집해 전송 (원격 백엔드는 로컬 저장소 접근 불가) ──────
    blame: GitCommitMeta | None = None
    # 이 파일을 건드린 커밋들(최신순). 각 커밋의 연관 이슈를 모아 중복 제거한다.
    # 비면 blame 단건으로 폴백(구버전 확장 호환).
    commits: list[CommitInput] = []
    branch: str = ""
    remoteUrl: str | None = None


class AttachmentMatch(BaseModel):
    """이슈에 첨부된 요구사항 문서 한 건."""
    label: str                  # 첨부 표시명/파일명
    url: str                    # 첨부 직접 링크
    pageCount: int | None = None  # PDF 등 페이지 수(미상이면 None)


class DocumentMatch(BaseModel):
    """역추적된 GitHub/GitLab Issue 한 건 (이슈 상세 화면의 단위).

    기존 호환 필드(title/url/matchType/confidence/excerpt)에 더해, 이슈 탭
    상세 화면(담당자·라벨·상태·첨부 목록 등)을 그릴 메타를 함께 싣는다.
    """
    title: str                  # Issue 제목
    url: str                    # Issue URL
    matchType: Literal["issue", "ticket", "semantic"] = "issue"
    confidence: float | None = None   # ticket=확정(None), semantic=0~1
    excerpt: str | None = None        # Issue 본문 일부 발췌(인용 블록)

    # ── 상세 화면 메타 (모두 옵셔널, 점진 도입) ─────────────────────────────
    issueNumber: int | None = None    # 이슈 번호(#N)
    state: str | None = None          # open / closed
    labels: list[str] = []            # 라벨명 목록(#spec 등)
    assignee: str | None = None       # 담당자 로그인/표시명
    createdAt: str | None = None      # 개설 ISO8601
    updatedAt: str | None = None      # 최근 수정 ISO8601
    commentCount: int | None = None   # 코멘트 수
    body: str | None = None           # 이슈 본문 전문(상세 화면 표시용)
    attachments: list[AttachmentMatch] = []  # 첨부 문서 목록


class TraceResponse(BaseModel):
    documents: list[DocumentMatch]
