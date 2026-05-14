import { createHttpClient } from '../../shared/http';
import { TraceRequest, TraceResult } from '../../shared/types';

/**
 * POST /api/trace/requirement — 코드에서 기획서 역추적.
 *
 * 👤 담당: 개발자 C
 */
export async function fetchRequirementTrace(req: TraceRequest): Promise<TraceResult> {
    const { data } = await createHttpClient().post<TraceResult>('/api/trace/requirement', req);
    return data;
}
