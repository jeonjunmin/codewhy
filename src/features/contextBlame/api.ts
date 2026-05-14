import { createHttpClient } from '../../shared/http';
import { BlameRequest, BlameResult } from '../../shared/types';

/**
 * POST /api/blame/context — 라인 단위 변경 사유 분석.
 *
 * 👤 담당: 개발자 A
 */
export async function fetchContextBlame(req: BlameRequest): Promise<BlameResult> {
    const { data } = await createHttpClient().post<BlameResult>('/api/blame/context', req);
    return data;
}
