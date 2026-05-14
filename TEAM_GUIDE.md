# 팀 작업 가이드

세 명이 동시에 작업해도 충돌이 나지 않도록 **기능별 폴더 단위로 코드 소유권을 분리**했습니다.
각 개발자는 원칙적으로 **자기 기능 폴더 안에서만** 파일을 만들고 고칩니다.

## 1. 담당 분장

| 담당      | 기능                | 프론트엔드                       | 백엔드                                      |
| -------- | ------------------- | -------------------------------- | ------------------------------------------- |
| 신예진    | Context Blame       | `src/features/contextBlame/`     | `backend/app/features/blame/`               |
| 박성태    | Timeline Summary    | `src/features/timelineSummary/`  | `backend/app/features/timeline/`            |
| 전준민    | Requirement Trace   | `src/features/requirementTrace/` | `backend/app/features/traceability/`        |

## 2. 폴더 구조

```
codewhy/
├── src/                              # VSCode 확장 (TypeScript)
│   ├── extension.ts                  # 진입점 — 세 기능 register 호출
│   ├── shared/                       # 공용 모듈 (수정 시 팀 합의 권장)
│   │   ├── http.ts                   #   axios 인스턴스
│   │   ├── editor.ts                 #   에디터 컨텍스트 추출
│   │   └── types.ts                  #   백엔드와 공유되는 응답 타입
│   └── features/
│       ├── contextBlame/             # 👤 A
│       │   ├── index.ts              #   명령 등록
│       │   ├── command.ts            #   진행/에러 처리 흐름
│       │   ├── api.ts                #   백엔드 호출
│       │   └── view.ts               #   화면 표시 (TODO: 디자인 반영)
│       ├── timelineSummary/          # 👤 B  (동일 구조)
│       └── requirementTrace/         # 👤 C  (동일 구조)
│
└── backend/app/                      # FastAPI (Python)
    ├── main.py                       # 진입점 — 세 라우터 include 호출
    ├── core/                         # 공용 모듈 (수정 시 팀 합의 권장)
    │   ├── config.py                 #   환경변수 로더
    │   ├── git.py                    #   git 저수준 헬퍼
    │   └── ai_client.py              #   Claude 클라이언트 래퍼
    ├── db/
    │   └── dynamodb.py               #   캐시 헬퍼 (A, B 가 사용)
    └── features/
        ├── blame/                    # 👤 A
        │   ├── router.py             #   FastAPI 라우터
        │   ├── service.py            #   비즈니스 로직 + 프롬프트
        │   └── schemas.py            #   요청/응답 모델
        ├── timeline/                 # 👤 B  (동일 구조)
        └── traceability/             # 👤 C  (동일 구조)
```

## 3. API 계약 (프론트 ↔ 백엔드)

세 엔드포인트의 요청/응답 형식은 이미 합의됐습니다.
프론트엔드 `src/shared/types.ts` 와 백엔드 `features/<name>/schemas.py` 의 키 이름이 **일치**해야 합니다.

| 엔드포인트                       | 담당 | 요청                          | 응답                                                                  |
| -------------------------------- | --- | ----------------------------- | --------------------------------------------------------------------- |
| `POST /api/blame/context`        | A   | `{filePath, line, repoPath}`  | `{explanation, commitHash, author, date}`                             |
| `POST /api/timeline/summary`     | B   | `{filePath, repoPath}`        | `{summary, milestones:[{date, description}]}`                         |
| `POST /api/trace/requirement`    | C   | `{filePath, line, repoPath}`  | `{documents:[{path, page, excerpt}]}`                                 |

응답 스키마를 바꾸려면 양쪽(types.ts + schemas.py)을 동시에 수정하세요.

## 4. 공용 코드 수정 규칙

다음 폴더는 세 명이 함께 쓰므로 **PR/합의 후** 건드립니다.

- `src/extension.ts`, `src/shared/**`
- `backend/app/main.py`, `backend/app/core/**`, `backend/app/db/**`
- `package.json` 의 `contributes.commands` 와 `menus`

새 명령을 추가하거나 응답 스키마를 바꿀 때 외에는 이 영역을 건드릴 일이 거의 없습니다.

## 5. 로컬 실행

```bash
# 1) 백엔드
cp backend/.env.example backend/.env       # 키 채우기
npm run backend:install
npm run backend:dev                        # http://localhost:8000

# 2) 프론트엔드 (다른 터미널)
npm install
npm run watch                              # tsc -watch
# VSCode 에서 F5 → Extension Development Host 실행
```

`/health` 가 200 을 반환하면 백엔드는 준비된 상태입니다.

## 6. 각자 첫 작업 체크리스트

각 개발자는 자기 폴더에서 다음을 채우면 됩니다.

### 공통
- [ ] `view.ts` 의 TODO 를 디자인 시안에 맞게 구현
- [ ] `service.py` 의 프롬프트/로직 다듬기

### 개발자 A (Context Blame)
- [ ] 인라인 표시(데코레이션) vs Webview 결정 후 `view.ts` 구현
- [ ] `service.py` 의 프롬프트 톤 조정

### 개발자 B (Timeline Summary)
- [ ] 마일스톤 타임라인 시각화 (Webview 추천)
- [ ] `service.py` 의 JSON 파싱 실패 시 폴백 처리

### 개발자 C (Requirement Trace)
- [ ] PDF / DOCX / XLSX 파서 추가 (`pypdf`, `python-docx`, `openpyxl` 등)
- [ ] QuickPick → Webview 미리보기 흐름 구현

---

질문이나 응답 스키마 변경이 필요하면 팀 채널에서 공유해주세요.
