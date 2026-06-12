import { createHttpClient } from '../../shared/http';
import { AskRequest, AskResult, BlameRequest, BlameResult, LineHistoryEntry } from '../../shared/types';

/**
 * POST /api/blame/context — 라인 단위 변경 사유 분석(비스트리밍, 폴백용).
 *
 * 👤 담당: 개발자 A
 */
export async function fetchContextBlame(req: BlameRequest): Promise<BlameResult> {
    const { data } = await createHttpClient().post<BlameResult>('/api/blame/context', req);
    return data;
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
 * POST /api/blame/ask — 현재 라인 블레임 맥락 위에서 후속 질문에 답한다.
 */
export async function askBlame(req: AskRequest): Promise<AskResult> {
    const { data } = await createHttpClient().post<AskResult>('/api/blame/ask', req);
    return data;
}
