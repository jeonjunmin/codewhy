/**
 * 백엔드 ↔ 프론트엔드 공용 응답 타입.
 *
 * 각 기능 폴더의 api.ts 에서 import 해 사용한다.
 * 백엔드 schemas.py 의 응답 모델과 키 이름이 일치해야 한다.
 */

// --- Context Blame (담당: 개발자 A) ---------------------------------
export interface BlameRequest {
    filePath: string;
    line: number;
    repoPath: string;
}

export interface BlameResult {
    explanation: string;
    commitHash: string;
    author: string;
    date: string;
    // "이름표 대신 사유서" 카드의 칩/AI 추론 영역용 — 백엔드 점진 도입을 위해 옵셔널
    ticket?: string;        // 예: "PAY-2041"
    specRef?: string;       // 예: "기획서 §4.2"
    team?: string;          // 예: "결제팀"
    aiSuggestion?: string;  // AI 개선 제안 한 문장

    // ── Context Blame 사이드바 추가 필드 (모두 옵셔널, 점진 도입) ──────────
    /** PR 단위 변경 통계 — 헤더 메타 테이블의 "변경" 칸. */
    changeStats?: { added: number; removed: number };
    /** 같은 PR의 총 라인 수 등 PR 컨텍스트 — "동일 PR 23 라인". */
    prInfo?: { url?: string; lines: number };
    /** 요구사항 출처 표시 — 예: "Issue #12: 결제 취소 정책 변경". */
    sourceRef?: string;
    /** 사이드바 '출처' 클릭 시 외부로 열 이슈 페이지 URL. */
    issueUrl?: string;
    /** 연관 이슈 본문에 첨부된 요구사항 문서 링크들. */
    attachments?: Attachment[];
    /** "이 변경과 함께 일어난 일" 섹션에 들어가는 관련 변경들. */
    relatedChanges?: RelatedChange[];
}

/** 연관 이슈에 첨부된 요구사항 문서 한 건. */
export interface Attachment {
    label: string;
    url: string;
}

/**
 * 같은 PR 또는 인접 시점에 일어난 관련 변경 한 건.
 * 사이드바의 "이 변경과 함께 일어난 일" 리스트에 한 행으로 렌더된다.
 */
export interface RelatedChange {
    kind: 'doc' | 'branch' | 'security' | 'commit';
    title: string;   // 예: "Issue #12: 결제 취소 정책 변경" / "auth_service.py 변경"
    meta: string;    // 예: "Issue #12" / "+78 라인 · 같은 PR"
}

/** "AI에게 더 묻기" — 현재 라인 블레임 맥락 위에서의 후속 질문. */
export interface AskRequest {
    filePath: string;
    line: number;
    repoPath: string;
    question: string;
}

export interface AskResult {
    answer: string;
}

// --- Timeline Summary (담당: 개발자 B) ------------------------------
export interface CommitInput {
    hash: string;
    author: string;
    date: string;    // YYYY-MM-DD
    subject: string;
}

export interface TimelineRequest {
    filePath: string;
    repoPath: string;
    commits: CommitInput[];   // 확장이 로컬 git log 를 수집해서 전송
}

export interface TimelineMilestone {
    date: string;
    description: string;
}

export interface TimelineResult {
    summary: string;
    milestones: TimelineMilestone[];
}

// --- Requirement Trace (담당: 개발자 C) -----------------------------
export interface TraceRequest {
    filePath: string;
    line: number;
    repoPath: string;
}

/**
 * 문서를 찾아낸 경로 — 신뢰도 표시에 쓴다.
 *   issue    : PR → Issue 직접 연결 (확정)
 *   ticket   : 커밋 메시지 티켓 번호로 Issue 매칭 (높음)
 *   semantic : 커밋 키워드로 관련 Issue 검색 (추정)
 */
export type TraceMatchType = 'issue' | 'ticket' | 'semantic';

export interface DocumentMatch {
    title: string;           // Issue 제목 또는 첨부 파일명
    url: string;             // Issue URL 또는 첨부 파일 직접 링크
    matchType?: TraceMatchType;
    confidence?: number;     // 0~1 (issue 확정 매칭은 없음)
    excerpt?: string;        // Issue 본문 일부 발췌
}

export interface TraceResult {
    documents: DocumentMatch[];
}
