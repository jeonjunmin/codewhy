# CodeWhy

> "코드의 이름표 대신 사유서를, 커밋 로그 대신 요점 정리를, 코드에서 기획서로 바로 가는 지름길을"

CodeWhy는 AI 기반 코드 이해 도구로, 개발자가 코드의 맥락과 의도를 쉽게 파악할 수 있도록 돕는 VSCode 확장 프로그램입니다.

---

## 핵심 기능

### 1. 컨텍스트 블레임 (Context Blame)

`git blame`은 **누가, 언제** 바꿨는지만 알려줍니다. CodeWhy는 AI가 **왜** 바꿨는지를 설명합니다.

```
기존: 홍길동이 3개월 전에 수정함
  →  홍길동 님이 3월 15일에 '해외 결제 시 수수료 3% 적용'이라는
     기획 내용을 반영하기 위해 이 코드를 추가했습니다.
```

에디터에서 원하는 라인을 선택한 뒤 우클릭 → **CodeWhy: 이 코드, 왜 바꿨어?**

### 2. 타임라인 요약 (Timeline Summary)

수백 개의 커밋을 직접 읽지 않아도 됩니다. AI가 파일의 변경 흐름을 한 문단으로 정리합니다.

```
이 파일은 1월에 처음 만들어졌고, 2월에는 로그인 기능이 추가됐으며,
3월에는 보안 강화를 위해 검증 로직이 한 번 엎어졌습니다.
```

에디터 우클릭 → **CodeWhy: 이 파일의 역사 요약**

### 3. 요구사항 역추적 (Requirement Traceability)

코드와 관련된 기획서를 자동으로 찾아줍니다.

- 선택한 라인의 커밋 메시지에서 키워드를 추출
- 지정한 문서 폴더에서 PDF · DOCX · XLSX · MD 파일을 검색
- 연관 발췌문과 함께 결과를 즉시 표시

에디터 우클릭 → **CodeWhy: 원본 기획서 찾기**

---

## 기술 스택

| 영역 | 기술 |
| --- | --- |
| VSCode 확장 | TypeScript, VSCode Extension API |
| 백엔드 | Python, FastAPI, Uvicorn |
| AI | Anthropic Claude API |
| Git | Git CLI (subprocess) |
| 캐시 | AWS DynamoDB |

---

## 시작하기

### 사전 요구사항

- Visual Studio Code 1.118.0 이상
- Node.js 18.x 이상
- Python 3.11 이상
- Git 2.25 이상

### 설치

```bash
git clone https://github.com/jeonjunmin/codewhy.git
cd codewhy
npm install
```

### 환경 설정

```bash
cp backend/.env.example backend/.env
```

`backend/.env` 를 열어 아래 값을 채웁니다.

```env
ANTHROPIC_API_KEY=sk-ant-...          # Claude API 키
AWS_ACCESS_KEY_ID=...                 # DynamoDB 접근용
AWS_SECRET_ACCESS_KEY=...
DOCUMENT_PATHS=/path/to/docs,/path/to/specs   # 기획서 폴더 경로
```

### 백엔드 실행

```bash
npm run backend:install   # Python 패키지 설치 (최초 1회)
npm run backend:dev       # http://localhost:8000 에서 서버 시작
```

`GET http://localhost:8000/health` 가 `{"status":"ok"}` 를 반환하면 정상입니다.

### 확장 실행 (개발 모드)

```bash
npm run watch   # 다른 터미널에서 TypeScript 감시 빌드
```

VSCode에서 `F5` → **Extension Development Host** 창이 열립니다.

### VSCode 설정

| 설정 키 | 설명 | 기본값 |
| --- | --- | --- |
| `codewhy.backendUrl` | 백엔드 서버 주소 | `http://localhost:8000` |
| `codewhy.documentPaths` | 기획서 폴더 경로 목록 | `[]` |

---

## 프로젝트 구조

```
codewhy/
├── src/                        # VSCode 확장 (TypeScript)
│   ├── extension.ts            # 진입점
│   ├── shared/                 # 공용 유틸리티
│   └── features/
│       ├── contextBlame/       # 컨텍스트 블레임
│       ├── timelineSummary/    # 타임라인 요약
│       └── requirementTrace/   # 요구사항 역추적
│
└── backend/app/                # FastAPI 백엔드 (Python)
    ├── main.py                 # 진입점
    ├── core/                   # 공용 모듈 (git, AI 클라이언트)
    ├── db/                     # DynamoDB 캐시
    └── features/
        ├── blame/              # 컨텍스트 블레임 API
        ├── timeline/           # 타임라인 요약 API
        └── traceability/       # 요구사항 역추적 API
```

팀 개발 가이드는 [TEAM_GUIDE.md](TEAM_GUIDE.md) 를 참고하세요.

---

## 팀

| 이름 | 담당 |
| --- | --- |
| 전준민 | 요구사항 역추적 |
| 신예진 | 컨텍스트 블레임 |
| 박성태 | 타임라인 요약 |

---

## 라이선스

MIT License
