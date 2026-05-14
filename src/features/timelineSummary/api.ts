import { createHttpClient } from '../../shared/http';
import { TimelineRequest, TimelineResult } from '../../shared/types';

/**
 * POST /api/timeline/summary — 파일 변경 이력 요약.
 *
 * 👤 담당: 개발자 B
 */
export async function fetchTimelineSummary(req: TimelineRequest): Promise<TimelineResult> {
    const { data } = await createHttpClient().post<TimelineResult>('/api/timeline/summary', req);
    return data;
}
