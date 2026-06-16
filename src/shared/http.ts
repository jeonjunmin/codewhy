import * as vscode from 'vscode';
import axios, { AxiosInstance } from 'axios';

/**
 * 백엔드(FastAPI) 서버 주소를 반환한다.
 *
 * 단일 출처(single source of truth)는 **package.json** 의
 * `contributes.configuration` → `codewhy.backendUrl` → `default` 한 곳뿐이다.
 * 사용자가 VS Code 설정에서 `codewhy.backendUrl` 을 지정하면 그 값이 우선한다.
 *
 * 코드에는 더 이상 URL 리터럴을 두지 않는다 — 서버 주소를 바꿀 땐 package.json 만 수정한다.
 */
export function getBackendUrl(): string {
    const config = vscode.workspace.getConfiguration('codewhy');
    // 사용자 설정이 없으면 config.get 이 package.json 의 기여(default)를 그대로 돌려준다.
    // 최종 fallback 역시 package.json 의 default 에서 읽어 리터럴 중복을 만들지 않는다.
    const fallback = config.inspect<string>('backendUrl')?.defaultValue ?? '';
    return config.get<string>('backendUrl', fallback);
}

/**
 * 백엔드(FastAPI) 호출용 axios 인스턴스를 생성한다.
 * baseURL 은 {@link getBackendUrl} 를 통해 매 호출마다 최신 설정값으로 읽는다.
 *
 * 각 기능은 자기 폴더의 api.ts 에서 이 함수를 통해 클라이언트를 받아 사용한다.
 */
export function createHttpClient(): AxiosInstance {
    return axios.create({ baseURL: getBackendUrl(), timeout: 120_000 });
}
