import * as vscode from 'vscode';
import axios, { AxiosInstance } from 'axios';

/**
 * 백엔드(FastAPI) 호출용 axios 인스턴스를 생성한다.
 * baseURL 은 `codewhy.backendUrl` 설정에서 읽으며, 기본값은 http://localhost:8000.
 *
 * 각 기능은 자기 폴더의 api.ts 에서 이 함수를 통해 클라이언트를 받아 사용한다.
 */
export function createHttpClient(): AxiosInstance {
    const config = vscode.workspace.getConfiguration('codewhy');
    const baseURL = config.get<string>('backendUrl', 'http://localhost:8000');
    return axios.create({ baseURL, timeout: 30000 });
}
