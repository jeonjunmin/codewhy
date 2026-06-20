import * as vscode from 'vscode';
import axios, { AxiosInstance } from 'axios';

/**
 * 확장의 실행 모드. activate() 에서 {@link initBackendMode} 로 1회 캡처한다.
 *
 * getBackendUrl() 은 여러 곳에서 ExtensionContext 없이 호출되므로,
 * 모드를 모듈 변수에 보관해두고 참조한다. 미초기화 시 안전하게 Production 으로 간주한다.
 */
let extensionMode: vscode.ExtensionMode = vscode.ExtensionMode.Production;

/** activate() 에서 호출해 실행 모드(Development/Production/Test)를 캡처한다. */
export function initBackendMode(mode: vscode.ExtensionMode): void {
    extensionMode = mode;
}

/**
 * 백엔드(FastAPI) 서버 주소를 반환한다.
 *
 * 두 개의 출처(single source of truth)는 모두 **package.json** 에 있다:
 *   - `codewhy.backendUrl`    → 설치본(.vsix/마켓플레이스)이 호출할 운영 서버(AWS)
 *   - `codewhy.devBackendUrl` → F5 개발 호스트가 호출할 로컬 서버(localhost)
 *
 * 코드에는 URL 리터럴을 두지 않는다 — 주소를 바꿀 땐 package.json 만 수정한다.
 */
export function getBackendUrl(): string {
    const config = vscode.workspace.getConfiguration('codewhy');

    // package.json 의 default 값들(리터럴 중복 방지를 위해 inspect 로 읽는다).
    const prodDefault = config.inspect<string>('backendUrl')?.defaultValue ?? '';
    const devDefault = config.inspect<string>('devBackendUrl')?.defaultValue ?? '';

    // 사용자가 설정 UI/settings.json 에서 backendUrl 을 직접 지정했는지(escape hatch).
    const inspected = config.inspect<string>('backendUrl');
    const userSet =
        inspected?.workspaceFolderValue ??
        inspected?.workspaceValue ??
        inspected?.globalValue;

    const isDev = extensionMode === vscode.ExtensionMode.Development;

    return resolveBackendUrl({ isDev, userSet, devDefault, prodDefault });
}

/**
 * 어떤 백엔드 URL 을 쓸지 결정하는 순수 함수.
 *
 * TODO(개발자): 아래 우선순위 정책을 구현해주세요. 고려할 점:
 *   - userSet (사용자가 backendUrl 을 직접 지정한 값)이 있으면 그걸 존중할까?
 *     → 자체 호스팅(self-hosted) 사용자의 escape hatch 가 됩니다. 다만 그러면
 *       개발 중에 settings.json 에 backendUrl 을 박아둔 사람은 로컬로 안 갈 수 있어요.
 *   - 개발 모드(isDev)면 devDefault(localhost), 설치본이면 prodDefault(AWS).
 *   - 어떤 값도 비어있을(빈 문자열) 가능성에 대한 방어.
 *
 * 반환: baseURL 로 사용할 최종 문자열.
 */
function resolveBackendUrl(opts: {
    isDev: boolean;
    userSet: string | undefined;
    devDefault: string;
    prodDefault: string;
}): string {
    // 정책 A — 모드 우선:
    // 개발 모드(F5)면 무조건 로컬, 설치본이면 사용자 지정값 > AWS default.
    if (opts.isDev) {
        return opts.devDefault || opts.prodDefault;
    }
    return opts.userSet || opts.prodDefault;
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
