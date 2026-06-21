import { createHttpClient } from '../../shared/http';
import { TimelineMilestone, TimelineRequest, TimelineResult } from '../../shared/types';

export interface TimelineStreamHandlers {
    onDelta(delta: string): void;
    onDone(result: TimelineResult): void;
    onError(message: string): void;
}

/**
 * POST /api/timeline/cache/clear — 현재 파일의 타임라인 요약 캐시를 백엔드에서 비운다.
 * 반환값은 삭제된 캐시 행 수.
 */
export async function clearTimelineCache(
    req: { filePath: string; repoPath: string },
): Promise<number> {
    const res = await createHttpClient().post('/api/timeline/cache/clear', req);
    return (res.data?.deleted as number) ?? 0;
}

/**
 * POST /api/timeline/summary 를 호출해 결과를 스트리밍으로 전달한다.
 *
 * 백엔드는 캐시 적중 시 application/json, 미스 시 text/event-stream(SSE) 으로 응답한다.
 * VSCode 확장 호스트(Node/Electron)에는 브라우저의 EventSource 가 없으므로,
 * axios 의 Node 스트림 응답(`responseType: 'stream'`)을 `for await...of` 로 직접 순회하며
 * `data: {...}\n\n` 프레임을 파싱해 onDelta/onDone 콜백으로 즉시 전달한다.
 *
 * 👤 담당: 개발자 B
 */
export async function streamTimelineSummary(
    req: TimelineRequest,
    handlers: TimelineStreamHandlers,
): Promise<void> {
    const response = await createHttpClient().post('/api/timeline/summary', req, {
        responseType: 'stream',
        headers: { Accept: 'text/event-stream, application/json' },
    });

    const contentType = String(response.headers['content-type'] ?? '');
    const body = response.data as AsyncIterable<Buffer>;

    if (contentType.includes('application/json')) {
        let raw = '';
        for await (const chunk of body) {
            raw += chunk.toString('utf-8');
        }
        handlers.onDone(JSON.parse(raw) as TimelineResult);
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

            let msg: { delta?: string; done?: boolean; error?: string; summary?: string; milestones?: TimelineMilestone[] };
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
                handlers.onDone({ summary: msg.summary ?? '', milestones: msg.milestones ?? [] });
                return;
            }
            if (typeof msg.delta === 'string') {
                handlers.onDelta(msg.delta);
            }
        }
    }
}
