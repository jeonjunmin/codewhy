import { createHttpClient } from '../../shared/http';
import { AskRequest, AskResult, BlameRequest, BlameResult, IssueChatRequest, LineHistoryEntry, LineIssue, ReasonRequest, ReasonResult } from '../../shared/types';

/**
 * POST /api/blame/context — 라인 단위 변경 사유 분석(비스트리밍, 폴백용).
 *
 * 👤 담당: 개발자 A
 */
export async function fetchContextBlame(req: BlameRequest): Promise<BlameResult> {
    const { data } = await createHttpClient().post<BlameResult>('/api/blame/context', req);
    return data;
}

/**
 * POST /api/blame/cache/clear — 현재 파일의 돋보기 설명 캐시를 백엔드에서 모두 비운다.
 * 반환값은 삭제된 캐시 행 수. (타임라인 clearTimelineCache 와 동일 패턴)
 */
export async function clearBlameCache(
    req: { filePath: string; repoPath: string },
): Promise<number> {
    const res = await createHttpClient().post('/api/blame/cache/clear', req);
    return (res.data?.deleted as number) ?? 0;
}

/** 스트리밍 첫 프레임(meta) — git 만으로 즉시 구하는 메타/라인 이력. */
export interface BlameMeta {
    commitHash: string;
    author: string;
    date: string;
    ticket?: string | null;
    team?: string | null;
    changeStats?: { added: number; removed: number };
    lineHistory?: LineHistoryEntry[];
    lineIssues?: LineIssue[];
}

export interface BlameStreamHandlers {
    onMeta(meta: BlameMeta): void;
    onDelta(delta: string): void;
    onDone(result: BlameResult): void;
    onError(message: string): void;
}

/**
 * POST /api/blame/context 를 호출해 결과를 스트리밍으로 전달한다.
 *
 * 백엔드는 캐시 적중/노이즈 커밋 시 application/json, 의미있는 커밋 미스 시
 * text/event-stream(SSE) 으로 응답한다(타임라인 /summary 와 동일한 듀얼 모드).
 * 타임라인 streamTimelineSummary 와 같은 방식으로 axios Node 스트림을 직접 순회하며
 * `data: {...}\n\n` 프레임을 파싱한다:
 *   - {"meta": {...}}            → onMeta (메타/라인 이력 즉시 렌더)
 *   - {"delta": "토큰"}          → onDelta (콜아웃 설명 타이핑)
 *   - {"done": true, ...result}  → onDone  (나머지 필드 확정)
 *   - {"error": "..."}           → onError
 *
 * 👤 담당: 개발자 A
 */
export async function streamContextBlame(
    req: BlameRequest,
    handlers: BlameStreamHandlers,
): Promise<void> {
    const response = await createHttpClient().post('/api/blame/context', req, {
        responseType: 'stream',
        headers: { Accept: 'text/event-stream, application/json' },
    });

    const contentType = String(response.headers['content-type'] ?? '');
    const body = response.data as AsyncIterable<Buffer>;

    // 캐시 적중/노이즈 — 단일 JSON 응답. 스트리밍 없이 곧장 최종 결과로 처리.
    if (contentType.includes('application/json')) {
        let raw = '';
        for await (const chunk of body) {
            raw += chunk.toString('utf-8');
        }
        handlers.onDone(JSON.parse(raw) as BlameResult);
        return;
    }

    let buffer = '';
    for await (const chunk of body) {
        buffer += chunk.toString('utf-8');

        let sepIndex: number;
        while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
            const frame = buffer.slice(0, sepIndex).trim();
            buffer = buffer.slice(sepIndex + 2);
            if (!frame.startsWith('data:')) { continue; }

            const payload = frame.slice('data:'.length).trim();
            if (!payload) { continue; }

            let msg: { meta?: BlameMeta; delta?: string; done?: boolean; error?: string } & Partial<BlameResult>;
            try {
                msg = JSON.parse(payload);
            } catch {
                continue;
            }

            if (msg.error) {
                handlers.onError(msg.error);
                return;
            }
            if (msg.done) {
                handlers.onDone(msg as BlameResult);
                return;
            }
            if (msg.meta) {
                handlers.onMeta(msg.meta);
                continue;
            }
            if (typeof msg.delta === 'string') {
                handlers.onDelta(msg.delta);
            }
        }
    }
}

/**
 * POST /api/blame/reason — 라인 수정 이력 항목을 펼칠 때 그 커밋의 변경 사유를 받아온다.
 *
 * /context 와 같은 (file_id, commit_id) 캐시를 공유하므로, 같은 커밋을 다시 펼치면 즉시 응답한다.
 *
 * 👤 담당: 개발자 A
 */
export async function fetchCommitReason(req: ReasonRequest): Promise<ReasonResult> {
    const { data } = await createHttpClient().post<ReasonResult>('/api/blame/reason', req);
    return data;
}

/**
 * POST /api/blame/ask — 현재 라인 블레임 맥락 위에서 후속 질문에 답한다.
 */
export async function askBlame(req: AskRequest): Promise<AskResult> {
    const { data } = await createHttpClient().post<AskResult>('/api/blame/ask', req);
    return data;
}

/** 이슈 챗봇 스트리밍 콜백 — streamContextBlame 의 핸들러 구조와 동일. */
export interface IssueChatStreamHandlers {
    onDelta(delta: string): void;
    onDone(): void;
    onError(message: string): void;
}

/**
 * POST /api/issue/chat 를 호출해 답변을 스트리밍으로 전달한다.
 *
 * 백엔드는 항상 SSE(text/event-stream)로 응답한다(timeline /summary 와 동일 프레임 규약):
 *   - {"delta": "토큰"}   → onDelta (말풍선에 누적)
 *   - {"done": true}      → onDone
 *   - {"error": "..."}    → onError
 * streamContextBlame 과 같은 방식으로 axios Node 스트림을 직접 순회하며 `data: {...}\n\n` 를 파싱한다.
 */
export async function streamIssueChat(
    req: IssueChatRequest,
    handlers: IssueChatStreamHandlers,
): Promise<void> {
    const response = await createHttpClient().post('/api/issue/chat', req, {
        responseType: 'stream',
        headers: { Accept: 'text/event-stream' },
    });

    const body = response.data as AsyncIterable<Buffer>;
    let buffer = '';
    for await (const chunk of body) {
        buffer += chunk.toString('utf-8');

        let sepIndex: number;
        while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
            const frame = buffer.slice(0, sepIndex).trim();
            buffer = buffer.slice(sepIndex + 2);
            if (!frame.startsWith('data:')) { continue; }

            const payload = frame.slice('data:'.length).trim();
            if (!payload) { continue; }

            let msg: { delta?: string; done?: boolean; error?: string };
            try {
                msg = JSON.parse(payload);
            } catch {
                continue;
            }

            if (msg.error) { handlers.onError(msg.error); return; }
            if (msg.done) { handlers.onDone(); return; }
            if (typeof msg.delta === 'string') { handlers.onDelta(msg.delta); }
        }
    }
    // 스트림이 done 프레임 없이 끝나면(연결 종료 등) 정상 종료로 간주.
    handlers.onDone();
}
