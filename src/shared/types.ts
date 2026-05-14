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
}

// --- Timeline Summary (담당: 개발자 B) ------------------------------
export interface TimelineRequest {
    filePath: string;
    repoPath: string;
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

export interface DocumentMatch {
    path: string;
    page: number;
    excerpt: string;
}

export interface TraceResult {
    documents: DocumentMatch[];
}
