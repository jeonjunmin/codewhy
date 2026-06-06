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
    /** 기획서 원문 위치 — 예: "2026_결제_기획서.pdf §4.2". */
    sourceRef?: string;
    /** "이 변경과 함께 일어난 일" 섹션에 들어가는 관련 변경들. */
    relatedChanges?: RelatedChange[];
}

/**
 * 같은 PR 또는 인접 시점에 일어난 관련 변경 한 건.
 * 사이드바의 "이 변경과 함께 일어난 일" 리스트에 한 행으로 렌더된다.
 */
export interface RelatedChange {
    kind: 'doc' | 'branch' | 'security' | 'commit';
    title: string;   // 예: "기획서 §4.2 조항 추가"
    meta: string;    // 예: "PAY-2041 · 김기획" / "+78 라인 · 같은 PR"
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

/** 문서를 찾아낸 경로 — 신뢰도 표시에 쓴다. ticket=확정, backfill/semantic=추정. */
export type TraceMatchType = 'ticket' | 'backfill' | 'semantic';

export interface DocumentMatch {
    documentId: number;
    name: string;            // 업로드 원본 파일명
    page?: number;
    excerpt?: string;
    downloadUrl: string;     // /api/documents/{id}/download
    matchType?: TraceMatchType;
    confidence?: number;     // 0~1 (ticket 매칭은 없음)
}

export interface TraceResult {
    documents: DocumentMatch[];
}
