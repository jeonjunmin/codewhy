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


class TraceRequest(BaseModel):
    filePath: str
    line: int
    repoPath: str
    # ── 확장이 로컬 git 으로 수집해 전송 (원격 백엔드는 로컬 저장소 접근 불가) ──────
    blame: GitCommitMeta | None = None
    branch: str = ""
    remoteUrl: str | None = None


class DocumentMatch(BaseModel):
    title: str                  # Issue 제목 또는 첨부 파일명
    url: str                    # Issue URL 또는 첨부 파일 URL
    matchType: Literal["issue", "ticket", "semantic"] = "issue"
    confidence: float | None = None   # ticket=확정(None), semantic=0~1
    excerpt: str | None = None        # Issue 본문 일부 발췌


class TraceResponse(BaseModel):
    documents: list[DocumentMatch]
