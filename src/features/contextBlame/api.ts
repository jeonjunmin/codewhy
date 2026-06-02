import { createHttpClient } from '../../shared/http';
import { AskRequest, AskResult, BlameRequest, BlameResult } from '../../shared/types';

/**
 * POST /api/blame/context — 라인 단위 변경 사유 분석.
 *
 * 👤 담당: 개발자 A
 */
export async function fetchContextBlame(req: BlameRequest): Promise<BlameResult> {
    const { data } = await createHttpClient().post<BlameResult>('/api/blame/context', req);
    return data;
}

/**
 * POST /api/blame/ask — 현재 라인 블레임 맥락 위에서 후속 질문에 답한다.
 */
export async function askBlame(req: AskRequest): Promise<AskResult> {
    const { data } = await createHttpClient().post<AskResult>('/api/blame/ask', req);
    return data;
}
