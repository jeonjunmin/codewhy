# CodeWhy VSIX 패키징 가이드

VSCode 확장 프로그램 설치 파일(`.vsix`)을 빌드하고 배포하는 방법을 설명합니다.

---

## 목차

1. [사전 준비](#1-사전-준비)
2. [아이콘 파일 준비](#2-아이콘-파일-준비)
3. [VSIX 빌드](#3-vsix-빌드)
4. [설치 방법](#4-설치-방법)
5. [분석 대상 저장소는 어떻게 결정되나](#5-분석-대상-저장소는-어떻게-결정되나)
6. [팀원 배포](#6-팀원-배포)
7. [버전 업데이트](#7-버전-업데이트)
8. [트러블슈팅](#8-트러블슈팅)

---

## 1. 사전 준비

### Node.js 및 npm 확인

```bash
node -v   # v18 이상 권장
npm -v
```

### vsce 설치

`vsce`는 VSCode 확장 패키징·배포 공식 CLI 도구입니다.

```bash
npm install -g @vscode/vsce
vsce --version  # 설치 확인
```

### 의존성 설치

```bash
npm install
```

---

## 2. 아이콘 파일 준비

> `images/icon-128.png` 파일이 준비되어 있으며, `package.json`에 이미 반영되어 있습니다.  
> 별도 작업 없이 바로 패키징을 진행할 수 있습니다.

```json
// package.json (현재 상태)
{
  "icon": "images/icon-128.png"
}
```

---

## 3. VSIX 빌드

### 빌드 전 검증

```bash
vsce ls
```

패키지에 포함될 파일 목록을 미리 확인합니다.  
`src/`, `node_modules/.../` 같은 불필요한 파일이 없는지 확인하세요.  
(`.vscodeignore`에서 제외 파일을 관리합니다.)

### VSIX 패키징

```bash
vsce package
```

성공 시 프로젝트 루트에 `codewhy-0.0.1.vsix` 파일이 생성됩니다.  
파일명 형식: `{name}-{version}.vsix`

#### 옵션

| 옵션 | 설명 |
|------|------|
| `--out ./dist/codewhy.vsix` | 출력 경로 지정 |
| `--no-dependencies` | node_modules 제외 (번들러 사용 시) |
| `--allow-star-activation` | `activationEvents: ["*"]` 허용 |

> **`repository` 필드는 일부러 넣지 않습니다.**  
> `package.json`의 `repository` 는 확장 *소스 코드*가 어디에 올라가 있는지를 알리는
> **패키징 메타데이터**일 뿐, 확장이 **분석하는** git 저장소와는 무관합니다.
> (분석 대상은 [5. 분석 대상 저장소는 어떻게 결정되나](#5-분석-대상-저장소는-어떻게-결정되나) 참고)
>
> `vsce` 는 `repository` 가 있을 때 README.md의 **상대 링크**를 마켓플레이스 절대 URL로
> 변환합니다. README에 해석 불가능한 상대 링크(`[텍스트](상대경로.md)`)가 없으면
> `repository` 필드 없이도 `vsce package` 가 정상 동작합니다. 이 프로젝트의 README는
> 상대 링크를 일반 텍스트로 두어 이 문제를 피했습니다.

---

## 4. 설치 방법

### 방법 A — VSCode UI

1. VSCode 사이드바에서 **Extensions** 탭 열기 (`Ctrl+Shift+X`)
2. 우측 상단 `···` 메뉴 클릭
3. **Install from VSIX...** 선택
4. `codewhy-0.0.1.vsix` 파일 선택
5. VSCode 재시작

### 방법 B — 커맨드라인

```bash
code --install-extension codewhy-0.0.1.vsix
```

### 설치 확인

```bash
code --list-extensions | findstr codewhy
# 출력: undefined_publisher.codewhy
```

---

## 5. 분석 대상 저장소는 어떻게 결정되나

> **확장이 분석하는 git 저장소는 VSCode에서 현재 열려 있는 폴더(프로젝트)로 자동 결정됩니다.**
> 설치 파일이나 설정 어디에도 저장소 경로가 하드코딩되어 있지 않습니다.

- 매번 명령(컨텍스트 블레임 / 타임라인 요약 / 요구사항 역추적)을 실행할 때,
  확장은 **현재 열린 워크스페이스 폴더**를 저장소 경로로 사용합니다.
  → `src/shared/editor.ts` 의 `getEditorContext()`:
  `vscode.workspace.workspaceFolders?.[0]?.uri.fsPath`
- 이 경로(`repoPath`)와 현재 파일 경로가 백엔드 요청에 실려 전달되고,
  백엔드는 해당 경로에서 `git` 명령을 실행합니다 (`backend/app/core/git.py`, `cwd=repoPath`).

### 사용자가 할 일

분석하려는 프로젝트 폴더를 **VSCode로 열기만 하면 됩니다.** 별도 저장소 설정은 없습니다.

### 주의 사항

- **폴더를 열지 않은 상태**(단일 파일만 열림)에서는 `repoPath` 가 비어 있어 git 분석이
  동작하지 않습니다. 반드시 *폴더/워크스페이스*로 여세요.
- **멀티 루트 워크스페이스**에서는 **첫 번째 폴더**(`workspaceFolders[0]`)가 사용됩니다.
  분석 대상 프로젝트를 첫 번째 폴더로 두세요.
- 연 폴더가 git 저장소(또는 그 하위)여야 합니다.

---

## 6. 팀원 배포

### 백엔드 서버 URL 안내

백엔드는 서버에 배포되어 있으므로 일반 팀원은 별도 설정이 필요 없습니다.
자체 호스팅 백엔드를 사용하는 경우에만 아래 설정을 VSCode에 추가합니다.

```json
// .vscode/settings.json 또는 사용자 설정
{
  "codewhy.backendUrl": "http://<백엔드-서버-IP>:8000"
}
```

### 배포 체크리스트

- [ ] `codewhy-x.x.x.vsix` 파일을 팀 공유 드라이브 또는 릴리스 페이지에 업로드
- [ ] 백엔드 서버 주소 공유
- [ ] Python 백엔드 실행 방법 안내 (`npm run backend:dev`)
- [ ] 필요 Python 패키지 설치 안내 (`npm run backend:install`)

---

## 7. 버전 업데이트

새 버전을 배포할 때마다 `package.json`의 `version`을 올립니다.

```bash
# 패치 버전 자동 증가 (0.0.1 → 0.0.2)
npm version patch

# 마이너 버전 증가 (0.0.1 → 0.1.0)
npm version minor

# 메이저 버전 증가 (0.0.1 → 1.0.0)
npm version major
```

이후 다시 패키징합니다.

```bash
vsce package
```

---

## 8. 트러블슈팅

### `ERROR: SVG icons are not supported`

```
ERROR  The 'icon' field value should not point to a .svg file.
```

**해결:** [아이콘 파일 준비](#2-아이콘-파일-준비) 섹션을 참고하여 PNG로 변환하세요.

---

### `The link '...' will be broken in README.md`

```
Couldn't detect the repository where this extension is published.
The link 'TEAM_GUIDE.md' will be broken in README.md.
```

README.md에 `vsce` 가 해석할 수 없는 **상대 링크**(`[텍스트](상대경로.md)`)가 있을 때
발생합니다. `repository` 필드가 없으면 상대 경로를 절대 URL로 바꿀 수 없기 때문입니다.

**해결 (권장):** README.md의 상대 링크를 일반 텍스트로 바꿉니다. 예)
`[PACKAGING_GUIDE.md](PACKAGING_GUIDE.md)` → `PACKAGING_GUIDE.md 파일`.
저장소 경로를 어디에도 박지 않으므로 가장 깔끔합니다.

> `repository` 필드를 추가하면 오류는 사라지지만, 확장 소스 저장소 URL이 설치 파일에
> 고정으로 들어갑니다. 분석 대상 저장소와는 무관하니 혼동하지 마세요.

---

### TypeScript 컴파일 오류

```bash
npm run compile
# 오류 메시지 확인 후 수정
```

`vsce package`는 내부적으로 `vscode:prepublish`(`npm run compile`)를 실행합니다.  
컴파일 오류가 있으면 패키징이 실패합니다.

---

### 확장이 활성화되지 않음

`out/extension.js`가 존재하는지 확인합니다.

```bash
ls out/
# extension.js 파일이 있어야 합니다.
```

없다면 수동 컴파일 후 재패키징합니다.

```bash
npm run compile
vsce package
```

---

### 백엔드 연결 오류

확장 설치 후 백엔드 서버가 실행 중인지 확인합니다.

```bash
# 백엔드 실행
npm run backend:install  # 최초 1회
npm run backend:dev      # 서버 시작 (port 8000)
```

VSCode 설정에서 `codewhy.backendUrl`이 올바른지 확인합니다.

---

## 빠른 참조

```bash
# 전체 빌드 & 패키징 한 번에
npm run compile && vsce package

# 설치
code --install-extension codewhy-0.0.1.vsix
```
