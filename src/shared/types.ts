/**
 * 백엔드 ↔ 프론트엔드 공용 응답 타입.
 *
 * 각 기능 폴더의 api.ts 에서 import 해 사용한다.
 * 백엔드 schemas.py 의 응답 모델과 키 이름이 일치해야 한다.
 */

// --- Context Blame (담당: 개발자 A) ---------------------------------

/**
 * 확장이 로컬 git 으로 뽑아 백엔드에 전달하는 커밋 메타 + diff.
 * 백엔드 schemas.py 의 GitCommitMeta 와 키가 일치해야 한다.
 * (백엔드를 원격에 올리면 로컬 저장소에 접근할 수 없어 git 실행을 클라이언트로 옮겼다.)
 */
export interface GitCommitMeta {
    commitHash: string;
    author: string;
    date: string;     // YYYY-MM-DD
    message: string;
    diff: string;
    added: number;
    removed: number;
}

/** blame 을 낼 수 없는 정상 상황 — 미커밋(uncommitted) / 이력 없음(no_history). */
export type BlameUnavailableReason = 'uncommitted' | 'no_history';

export interface BlameRequest {
    filePath: string;
    line: number;
    repoPath: string;
    // ── 확장이 로컬 git 으로 수집해 전송하는 데이터 ──────────────────────
    /** blamed 커밋 메타. unavailable 이 있으면 null. */
    blame: GitCommitMeta | null;
    /** blame 불가 사유(미커밋 등). 정상이면 null. */
    unavailable: BlameUnavailableReason | null;
    /** 현재 브랜치명 — 백엔드 티켓 추출(메시지+브랜치)에 쓴다. */
    branch: string;
    /** 이 라인이 실제로 바뀐 커밋들(최신순) — 백엔드가 이슈 롤업을 덧붙인다. */
    lineHistory: CommitInput[];
    /** 같은 티켓 후속 커밋 — '함께 일어난 일' 재료. */
    followups: CommitInput[];
    /** origin remote URL 원문 — 백엔드가 파싱해 PR/이슈 API 조회에 쓴다. */
    remoteUrl: string | null;
}

export interface BlameResult {
    explanation: string;
    /** explanation 이 Bedrock 추론이 아니라 폴백(호출 실패 등)이면 true.
     *  true 면 일시적 실패이므로 클라이언트 캐시에 담지 않아 다음 시도에 재호출된다. */
    aiDegraded?: boolean;
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
    /** 사이드바 하단 "라인 수정 이력" — 이 라인이 실제로 바뀐 커밋들(최신순). */
    lineHistory?: LineHistoryEntry[];
    /** 라인 전체에서 dedup 된 연관 이슈 롤업(현재/과거/되돌림). */
    lineIssues?: LineIssue[];
}

/**
 * "라인 수정 이력" 한 행 — 이 라인이 실제로 바뀐 커밋 하나.
 * 백엔드 schemas.py 의 LineHistoryEntry 와 키가 일치해야 한다.
 */
export interface LineHistoryEntry {
    hash: string;        // 전체 해시 (UI 에서 7자리로 축약)
    author: string;
    date: string;        // YYYY-MM-DD
    subject: string;     // 커밋 제목
    issueCount: number;  // 참조 이슈 수 — 0 이면 배지 숨김
    /** 항목을 펼칠 때 /api/blame/reason 으로 지연 채워지는 그 커밋의 AI 변경 사유. */
    reason?: string | null;
}

/** 라인 관점 이슈 상태 — 현재 driver / 과거 / 되돌림(휴리스틱). */
export type LineIssueStatus = 'current' | 'past' | 'reverted';

/**
 * "라인 수정 이력" 전체에서 dedup 된 연관 이슈 한 건(이슈 롤업 칩).
 * 백엔드 schemas.py 의 LineIssue 와 키가 일치해야 한다.
 */
export interface LineIssue {
    number: number;          // 이슈 번호 (#N 의 N)
    status: LineIssueStatus;
    changeCount: number;     // 이 이슈가 등장한 라인-이력 커밋 수
    url?: string | null;     // 해석되면 GitHub 이슈 URL (담당: 개발자 C)
    title?: string | null;   // 해석되면 이슈 제목 (담당: 개발자 C)
}

/** POST /api/blame/reason — 라인 이력 항목 펼침 시 그 커밋의 변경 사유 요청. */
export interface ReasonRequest {
    filePath: string;
    repoPath: string;
    hash: string;
    // ── 확장이 로컬 git 으로 수집해 전송하는 데이터 ──────────────────────
    /** 해당 커밋의 메타 + diff. 해시가 유효하지 않으면 null. */
    commit: GitCommitMeta | null;
    /** 현재 브랜치명(티켓 추출용). */
    branch: string;
    /** 같은 티켓 후속 커밋. */
    followups: CommitInput[];
    /** origin remote URL 원문. */
    remoteUrl: string | null;
}

export interface ReasonResult {
    reason: string;
    aiDegraded?: boolean;
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
    // ── 확장이 로컬 git 으로 수집해 전송하는 데이터 ──────────────────────
    /** 현재 라인 blamed 커밋 메타. 미커밋이면 null. */
    blame: GitCommitMeta | null;
    /** blame 불가 사유. 정상이면 null. */
    unavailable: BlameUnavailableReason | null;
    /** origin remote URL 원문. */
    remoteUrl: string | null;
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
    /** @deprecated 파일 단위 추적으로 전환 — 로깅/하위호환용으로만 남는다. */
    line: number;
    repoPath: string;
    // ── 확장이 로컬 git 으로 수집해 전송 (원격 백엔드는 로컬 저장소 접근 불가) ──────
    /** 대표 커밋 메타(파일 최신 커밋). commits 가 비었을 때의 단일-커밋 폴백용. */
    blame: GitCommitMeta | null;
    /**
     * 이 파일을 건드린 커밋들(최신순). 백엔드가 각 커밋에서 연관 이슈를 모아
     * 중복 제거해 '파일 단위' 연관 이슈 목록을 만든다. 비면 blame 단건으로 폴백.
     */
    commits: CommitInput[];
    /** 현재 브랜치명(티켓 추출용). */
    branch: string;
    /** origin remote URL 원문 — 백엔드가 파싱해 GitHub/GitLab API 조회에 쓴다. */
    remoteUrl: string | null;
}

/**
 * 문서를 찾아낸 경로 — 신뢰도 표시에 쓴다.
 *   issue    : PR → Issue 직접 연결 (확정)
 *   ticket   : 커밋 메시지 티켓 번호로 Issue 매칭 (높음)
 *   semantic : 커밋 키워드로 관련 Issue 검색 (추정)
 */
export type TraceMatchType = 'issue' | 'ticket' | 'semantic';

/** 이슈에 첨부된 요구사항 문서 한 건 (이슈 상세 화면의 첨부 목록). */
export interface IssueAttachment {
    label: string;
    url: string;
    pageCount?: number | null;   // PDF 등 페이지 수(미상이면 null)
}

/** 이슈 활동 타임라인 한 항목 — 사람 코멘트 또는 시스템 이벤트. */
export interface IssueComment {
    kind: 'comment' | 'event';
    author?: string | null;        // 작성자(comment) / 행위자(event)
    createdAt?: string | null;     // ISO8601
    body?: string | null;          // comment 본문 (gitlab system note 는 event 본문)
    event?: string | null;         // labeled/assigned/committed/referenced/closed/reopened/note
    label?: string | null;         // labeled 이벤트의 라벨명
    assignee?: string | null;      // assigned 이벤트의 담당자
    commitSha?: string | null;     // committed/referenced 이벤트의 커밋 해시
    commitSummary?: string | null; // 커밋 메시지 첫 줄
    attachments?: IssueAttachment[];
}

export interface DocumentMatch {
    title: string;           // Issue 제목
    url: string;             // Issue URL
    matchType?: TraceMatchType;
    confidence?: number;     // 0~1 (issue 확정 매칭은 없음)
    excerpt?: string;        // Issue 본문 일부 발췌(인용 블록)

    // ── 상세 화면 메타 (모두 옵셔널, 백엔드 점진 도입) ──────────────────────
    issueNumber?: number | null;   // 이슈 번호(#N)
    state?: string | null;         // open / closed
    labels?: string[];             // 라벨명(#spec 등)
    assignee?: string | null;      // 담당자
    createdAt?: string | null;     // 개설 ISO8601
    updatedAt?: string | null;     // 최근 수정 ISO8601
    commentCount?: number | null;  // 코멘트 수
    body?: string | null;          // 이슈 본문 전문
    attachments?: IssueAttachment[];
    comments?: IssueComment[];     // 활동 타임라인(코멘트+시스템 이벤트, 시간순)
}

export interface TraceResult {
    documents: DocumentMatch[];
}
