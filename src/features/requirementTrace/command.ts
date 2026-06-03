import * as vscode from 'vscode';
import { getEditorContext } from '../../shared/editor';
import { fetchRequirementTrace } from './api';
import { showRequirementTraceView } from './view';

/**
 * `codewhy.requirementTrace` 명령 핸들러.
 *
 * 1. `codewhy.documentPaths` 설정이 있는지 확인하고
 * 2. 현재 파일/라인/레포 경로를 얻어 백엔드에 검색을 요청하고
 * 3. 결과를 view 모듈에 전달한다.
 *
 * 👤 담당: 개발자 C
 */
export async function runRequirementTrace(context: vscode.ExtensionContext) {
    const config = vscode.workspace.getConfiguration('codewhy');
    const documentPaths = config.get<string[]>('documentPaths', []);
    // TODO(결정): 브라운필드 모델에서는 문서가 서버에 업로드되므로 로컬 documentPaths 가 불필요하다.
    //   이 게이트가 비어 있으면 추적이 시작도 안 되어 백필/시맨틱 경로를 막는다.
    //   게이트를 제거하거나 "서버 문서 사용" 모드로 전환할지 결정 필요.
    if (documentPaths.length === 0) {
        vscode.window.showWarningMessage(
            'CodeWhy: 기획서 폴더가 설정되지 않았습니다. 설정에서 codewhy.documentPaths를 추가하세요.'
        );
        return;
    }

    const ctx = getEditorContext();
    if (!ctx) {
        return;
    }

    await vscode.window.withProgress(
        {
            location: vscode.ProgressLocation.Notification,
            title: 'CodeWhy: 연관 기획서 검색 중...',
        },
        async () => {
            try {
                const result = await fetchRequirementTrace(ctx);
                if (result.documents.length === 0) {
                    vscode.window.showInformationMessage('연관된 기획 문서를 찾지 못했습니다.');
                    return;
                }
                showRequirementTraceView(context, ctx, result);
            } catch (err) {
                vscode.window.showErrorMessage(
                    `Requirement Trace 실패: ${(err as Error).message}`
                );
            }
        }
    );
}
