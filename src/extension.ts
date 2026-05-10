import * as vscode from 'vscode';

export function activate(context: vscode.ExtensionContext) {
    // 말풍선(Hover) 등록
    let hoverProvider = vscode.languages.registerHoverProvider('*', {
        provideHover(document, position, token) {
            // 마우스가 올려진 단어 가져오기
            const range = document.getWordRangeAtPosition(position);
            const word = document.getText(range);

            // 말풍선 내용 구성 (Markdown 지원)
            const contents = new vscode.MarkdownString();
            contents.appendMarkdown(`### 🎈 말풍선 가이드\n`);
            contents.appendMarkdown(`현재 선택한 단어: **${word}**\n\n`);
            contents.appendMarkdown(`여기에 원하는 설명을 넣을 수 있어요.`);

            return new vscode.Hover(contents);
        }
    });

    context.subscriptions.push(hoverProvider);
}

export function deactivate() {}