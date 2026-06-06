# CodeWhy 개발 가이드

> 현재 소스 코드(`master` 기준) 기반으로 정리한 **단일 개발 레퍼런스**입니다.
> 개발 현황·구조·담당 분장·커밋 규칙·TODO를 한 곳에서 관리합니다.
> 마지막 정리: 2026-06-06

---

## 목차

| # | 섹션 | 한 줄 요약 |
|---|------|-----------|
| 1 | [프로젝트 개요](#1-프로젝트-개요) | CodeWhy가 뭔지, 3대 기능 |
| 2 | [담당 분장](#2-담당-분장) | 기능별 폴더 소유권 |
| 3 | [아키텍처](#3-아키텍처) | 확장 ↔ 백엔드 ↔ 외부 시스템 |
| 4 | [기술 스택](#4-기술-스택) | TS, FastAPI, PostgreSQL, Bedrock |
| 5 | [디렉터리 구조](#5-디렉터리-구조) | 폴더 트리 |
| 6 | [기능 현황](#6-기능-현황) | 각 기능 완성도와 동작 흐름 |
| 7 | [데이터 모델](#7-데이터-모델-통합-스키마-6테이블) | 6테이블 ERD |
| 8 | [API 엔드포인트](#8-api-엔드포인트-요약) | 프론트↔백 통신 규격 |
| 9 | [로컬 개발 환경](#9-로컬-개발-환경) | 설치, 실행, 환경변수, DB 마이그레이션 |
| 10 | [TODO 리스트](#10-todo-리스트) | 미완성·개선·검증 항목 모음 |
| 11 | [커밋 메시지 규칙](#11-커밋-메시지-규칙) | conventional-commit + 도메인 태그 |
| 12 | [기여 시 참고](#12-기여-시-참고) | 기능/공용 코드 추가 규칙 |

---

## 1. 프로젝트 개요

CodeWhy는 **"코드의 왜(why)를 설명하는"** VSCode 확장 + Python 백엔드입니다.
`git blame`이 *누가/언제*만 알려주는 한계를, AI(RAG)로 *왜 바꿨는지·기획 의도*까지 메워 줍니다.

| 기능 | 한 줄 설명 | 담당 |
| --- | --- | --- |
| 컨텍스트 블레임 (Context Blame) | 선택한 라인의 변경 *이유*를 GitHub Issue 기획 근거와 함께 설명 | 신예진 |
| 타임라인 요약 (Timeline Summary) | 파일의 전체 커밋 흐름을 한 문단 + 마일스톤으로 요약 | 박성태 |
| 요구사항 역추적 (Requirement Trace) | 코드 → PR → GitHub Issue 첨부 기획 문서를 연결해 보여줌 | 전준민 |

추가로 **브라운필드 온보딩**(레거시 레포 일괄 적재) 기능이 개발 중입니다.

### 요구사항 문서 연결 방식 (최종 결정)

세 기능 모두 **GitHub Issue에 업로드된 첨부 파일**을 요구사항 문서로 활용합니다.

```
코드 라인 → git blame → commit → PR → PR 본문에서 Issue 번호 파싱
    → GitHub Issue 본문 + 첨부 파일(PDF, DOCX 등) 수집
    → AI 설명 / 역추적 결과에 출처로 표시
```

별도 문서 업로드·파싱 없이, 팀이 이미 쓰고 있는 GitHub Issue 워크플로우에 기획서를 첨부하면 자동으로 코드와 연결됩니다.

---

## 2. 담당 분장

세 명이 동시에 작업해도 충돌이 나지 않도록 **기능별 폴더 단위로 코드 소유권을 분리**했습니다.
각 개발자는 원칙적으로 **자기 기능 폴더 안에서만** 파일을 만들고 고칩니다.

| 담당 | 기능 | 프론트엔드 | 백엔드 |
| ---- | ---- | ---------- | ------ |
| 신예진 | Context Blame | `src/features/contextBlame/` | `backend/app/features/blame/` |
| 박성태 | Timeline Summary | `src/features/timelineSummary/` | `backend/app/features/timeline/` |
| 전준민 | Requirement Trace | `src/features/requirementTrace/` | `backend/app/features/traceability/` |

---

## 3. 아키텍처

```
┌──────────────────────┐      HTTP(axios)      ┌──────────────────────────┐
│  VSCode 확장 (TS)     │ ───────────────────▶ │  FastAPI 백엔드 (Python)   │
│  src/                │                       │  backend/app/             │
│  - 우클릭 명령 3종     │ ◀─────────────────── │  - /api/blame             │
│  - Webview 사이드바    │      JSON 응답         │  - /api/timeline          │
└──────────────────────┘                       │  - /api/trace             │
                                               │  - /api/onboarding        │
                                               └───────────┬──────────────┘
                                                           │
                          ┌────────────────────────────────┼─────────────────────────┐
                          ▼                                ▼                           ▼
                  ┌───────────────┐              ┌──────────────────┐        ┌──────────────────┐
                  │  Git CLI       │              │ PostgreSQL (RDS) │        │  AWS Bedrock      │
                  │  (subprocess)  │              │  통합 스키마       │        │  - Converse(LLM)  │
                  │  blame/log/diff│              │  + 캐시           │        │  - LangGraph 요약  │
                  └───────────────┘              └──────────────────┘        └──────────────────┘
                                                                                      │
                                               ┌──────────────────┐                   │
                                               │  GitHub API       │ ◀────────────────┘
                                               │  - PR 조회         │   (Issue 본문·첨부를
                                               │  - Issue 본문/첨부  │    LLM 맥락으로 전달)
                                               └──────────────────┘
```

**데이터 흐름의 공통 패턴**:

1. 확장이 로컬 git 정보(repoPath/filePath/line 등)를 백엔드로 전송
2. 백엔드가 git CLI로 blamed 커밋 추출
3. GitHub API로 PR → Issue → 첨부 파일 수집
4. PostgreSQL 캐시 조회 — 적중 시 즉시 반환
5. 캐시 미스 시 Bedrock LLM 호출 (코드 + 커밋 + Issue 맥락 종합)
6. 결과 캐시 후 반환

---

## 4. 기술 스택

| 영역 | 기술 |
| --- | --- |
| VSCode 확장 | TypeScript 5.9, VSCode Extension API (`^1.118.0`), axios |
| 백엔드 | Python 3.11+, FastAPI, Uvicorn, Pydantic v2 / pydantic-settings |
| DB | PostgreSQL(RDS), SQLAlchemy 2.0(async/asyncpg), Alembic(psycopg2) |
| AI | AWS Bedrock — Converse API(LLM), LangGraph(타임라인 Map-Reduce) |
| VCS 연동 | GitHub REST API (PR·Issue·첨부 조회), GitLab MR API (계획) |
| Git | Git CLI subprocess (`app/core/git.py`) |

---

## 5. 디렉터리 구조

```
codewhy/
├── src/                              # VSCode 확장 (TypeScript)
│   ├── extension.ts                  # 진입점 — 기능 register 호출만
│   ├── shared/                       # http 클라이언트, editor 유틸, 공용 타입
│   └── features/
│       ├── contextBlame/             # 신예진 — command / view(webview) / api / sidebar
│       ├── timelineSummary/          # 박성태 — command / view / api
│       └── requirementTrace/         # 전준민 — command / view / api
│
└── backend/app/                      # FastAPI 백엔드
    ├── main.py                       # 앱 생성 + 라우터 등록 + DB 연결 확인
    ├── core/                         # 공용 모듈
    │   ├── git.py                    # blame/log/diff/branch 추출
    │   ├── ai_client.py / bedrock.py # Bedrock Converse / LangChain ChatBedrock
    │   ├── config.py                 # pydantic-settings 환경설정
    │   ├── tickets.py                # 커밋/파일명에서 티켓(PAY-2041) 추출
    │   └── vcs.py                    # GitHub/GitLab PR·Issue·첨부 조회
    ├── db/
    │   ├── models.py                 # 통합 스키마 ORM
    │   ├── postgres.py               # async engine / get_db / Base
    │   └── crud_common.py            # repo/commit/file 공유 백본 upsert
    ├── ai/graph.py                   # LangGraph 타임라인 Map-Reduce 파이프라인
    ├── alembic/versions/             # DB 마이그레이션
    └── features/
        ├── blame/                    # 신예진 — router/service/crud/schemas
        ├── timeline/                 # 박성태 — router/service/crud/graph/schemas
        ├── traceability/             # 전준민 — router/service/schemas
        └── onboarding/               # router/backfill/schemas (브라운필드 백필)
```

---

## 6. 기능 현황

### ✅ 컨텍스트 블레임 (Context Blame) — 동작 가능

`POST /api/blame/context`, `POST /api/blame/ask`

**무엇을 하나**: 사용자가 클릭한 코드 라인의 "왜 바꿨는지"를 AI가 추론해 사이드바에 보여준다.

- git blame → 커밋 해석 → PR에서 연결된 GitHub Issue + 첨부 문서 수집
- 코드 + 커밋 + Issue 맥락을 Bedrock Converse로 종합 → **변경 사유 한국어 설명**
- 부가: 티켓/팀 매핑, 같은 티켓 후속 커밋 → "함께 일어난 일" 조립
- "AI에게 더 묻기"(`/ask`) 후속 질문 지원
- **캐시**: `blame_explanations` UNIQUE(**file_id, commit_id**) — 커밋×파일 단위
- Bedrock 미설정 시 커밋 메시지로 폴백 → 로컬에서도 깨지지 않음

#### 구현 흐름 상세

> 핵심 설계 원칙: **"왜 바뀌었나"는 줄(line)이 아니라 커밋이 그 파일에 가한 변경의 속성이다.**
> 줄 번호는 `git blame`으로 커밋을 찾기 위한 포인터일 뿐이므로, 분석·저장 단위를 **커밋×파일**로 잡는다.
> 같은 커밋이 바꾼 여러 줄은 변경 이유가 같으므로 설명 1개를 공유한다(Bedrock 호출 1회, DB 행 1개).

```
사용자가 라인 클릭
    │
    ▼
[1] git blame  →  이 줄을 마지막으로 바꾼 commit_hash 해석
    │
    ▼
[2] 공유 백본 upsert  →  repo/file/commit/commit_files 행 확보 (멱등)
    │
    ▼
[3] 캐시 조회  →  blame_explanations WHERE (file_id, commit_id)
    │
    ├─ 적중 ──▶  저장된 설명 즉시 반환  (Bedrock 0회)
    │
    └─ 미스 ──▶
            [4] service.analyze_blame
                  ① PR 조회 → PR 본문에서 Issue 번호 파싱
                  ② GitHub Issue 본문 + 첨부 파일 URL 수집
                  ③ _build_context(커밋+Issue 맥락) 블록 생성
                  ④ call_bedrock(설명)   ← Bedrock 1회
                  ⑤ call_bedrock(AI제안) ← 프롬프트 캐시 적중
                  ⑥ 후속커밋 조립(relatedChanges)
            [5] blame_explanations upsert
            [6] 응답 반환
```

**캐시 무효화**: 줄이 새 커밋으로 수정되면 `git blame`이 다른 commit_hash를 반환 → 캐시 키 불일치 → 자동 재분석. TTL 없음.

**비용 최적화**:
1. diff 길이 제한 (`_MAX_DIFF_CHARS = 2000`) — 거대 커밋의 토큰 폭발 방지
2. 프롬프트 캐싱 — 설명+AI제안이 같은 context 블록 공유, 두 번째 호출은 Bedrock 캐시 적중

### ⚠️ 타임라인 요약 (Timeline Summary) — 캐시 키 미구현 (블로커)

`POST /api/timeline/...`

- LangGraph **Map-Reduce 파이프라인** 완성(`ai/graph.py`)
- 노이즈 커밋(test/chore/docs) 제거, 청크 20개 단위, JSON 파싱 실패 시 최대 2회 재시도
- **캐시**: `timeline_summaries` UNIQUE(file_id, commit_set_hash)
- 🚫 **`compute_commit_set_hash()`가 `NotImplementedError`** → 타임라인 호출 실패 (→ [§10 TODO](#-블로커--기능-동작에-필수))

### ✅ 요구사항 역추적 (Requirement Trace) — 동작 가능

`POST /api/trace/requirement`

코드 라인에서 연관 기획 문서를 찾아 보여주는 기능. **GitHub Issue 첨부 파일**이 기획 문서의 원천입니다.

**추적 경로**:

```
코드 라인 → git blame → commit → PR 조회
    → PR 본문에서 "Closes #N", "Fixes #N" 파싱
    → GitHub Issue 본문 + 첨부 파일(PDF, DOCX 등) 수집
    → 결과를 matchType/confidence 와 함께 UI에 표시
```

| matchType | 방식 | 확신도 |
|-----------|------|--------|
| `issue` | PR → Issue 직접 연결, 첨부 파일 있음 | 확정 |
| `ticket` | 커밋 메시지의 티켓 번호로 Issue 매칭 | 높음 |
| `semantic` | 커밋 메시지 키워드로 관련 Issue 검색 | 추정 (낮음) |

### 🧪 브라운필드 온보딩 (Onboarding) — 개발 중

`POST /api/onboarding/backfill`

- 레거시 레포 전체 git 히스토리를 훑어 커밋↔Issue 역링크를 사전 생성
- 부분 유니크 인덱스로 재실행 중복 방지(idempotent)

---

## 7. 데이터 모델 (통합 스키마 6테이블)

```
repositories ─┬─ commits ─┬─ commit_files ─ files
              │           │
  blame_explanations ─────┘         timeline_summaries
```

| 테이블 | 역할 | 핵심 제약 |
| --- | --- | --- |
| `repositories` | 레포 식별자 루트 | identifier UNIQUE |
| `commits` | git 커밋 (블레임·타임라인 공유) | UNIQUE(repo_id, commit_hash) |
| `files` | 레포 내 파일 경로 | UNIQUE(repo_id, file_path) |
| `commit_files` | 커밋↔파일 N:M + 변경량 | (commit_id, file_id) PK |
| `blame_explanations` | 블레임 AI 결과 캐시 | UNIQUE(file_id, commit_id) |
| `timeline_summaries` | 타임라인 요약 캐시 | UNIQUE(file_id, commit_set_hash) |

> **`documents` / `document_links` 테이블 삭제 (2026-06-06)**
> 요구사항 문서를 별도 업로드·저장하던 방식에서, **GitHub Issue 첨부 파일을 실시간 조회**하는 방식으로 전환했습니다.
> - 문서 메타데이터를 DB에 저장할 필요 없음 — GitHub API가 원천
> - 커밋↔문서 매핑도 DB에 저장할 필요 없음 — `commit → PR → Issue → attachments` 체인으로 파생
> - 분석 결과(출처 URL 포함)는 `blame_explanations`의 JSON 컬럼에 이미 캐시됨
>
> 기존에 이 테이블을 사용하던 코드(`features/documents/`, `features/onboarding/backfill.py`의 `_link_passages`)는 정리 대상입니다.

**설계 원칙**:
- 세 기능이 공유하는 데이터(작성자·날짜·메시지·티켓)는 `commits`/`files`에 한 번만 저장하고, 기능별 산출물은 FK로 참조.
- 스키마 변경은 **반드시 Alembic autogenerate 마이그레이션**으로.

---

## 8. API 엔드포인트 요약

> 프론트엔드 `src/shared/types.ts`와 백엔드 `features/<name>/schemas.py`의 키 이름이 **일치**해야 합니다.
> 응답 스키마를 바꾸려면 양쪽을 동시에 수정하세요.

| Method | Path | 담당 | 요청 | 응답 |
| --- | --- | --- | --- | --- |
| GET | `/health` | — | — | 헬스체크 |
| POST | `/api/blame/context` | 신예진 | `{filePath, line, repoPath}` | `{explanation, commitHash, author, date, ...}` |
| POST | `/api/blame/ask` | 신예진 | `{filePath, line, repoPath, question}` | `{answer}` |
| POST | `/api/timeline/summary` | 박성태 | `{filePath, repoPath}` | `{summary, milestones:[{date, description}]}` |
| POST | `/api/trace/requirement` | 전준민 | `{filePath, line, repoPath}` | `{documents:[{title, url, matchType, confidence}]}` |
| POST | `/api/onboarding/backfill` | — | `{repoPath}` | 레포 전체 커밋↔Issue 백필 |

---

## 9. 로컬 개발 환경

### 사전 요구사항

- VSCode 1.118.0+, Node.js 18+, Python 3.11+, Git 2.25+
- PostgreSQL (로컬 또는 RDS), 선택적으로 AWS Bedrock 자격증명

### 설치 & 실행

```bash
npm install
cp backend/.env.example backend/.env      # 값 채우기

npm run backend:install                    # pip install (최초 1회)
npm run backend:dev                        # http://localhost:8000

npm run watch                              # 다른 터미널: TS 감시 빌드
# VSCode F5 → Extension Development Host
```

`/health`가 200을 반환하면 백엔드 준비 완료.

### 주요 환경변수 (`backend/.env`)

| 키 | 용도 | 미설정 시 |
| --- | --- | --- |
| `DATABASE_URL` | PostgreSQL 접속 | localhost 기본값 |
| `AWS_ACCESS_KEY_ID/SECRET/SESSION_TOKEN` | Bedrock 자격증명 | `~/.aws` 폴백 |
| `BEDROCK_MODEL_ID` | LLM 모델 ID | 기본 Claude 3.5 Sonnet |
| `GITHUB_TOKEN` | GitHub PR·Issue·첨부 조회 | PR/Issue 연동 생략 |
| `GITLAB_TOKEN` | GitLab MR 조회 (계획) | MR 연동 생략 |
| `CODEWHY_TEAM_MAP` | 작성자→팀 매핑 JSON 경로 | team 칸 생략 |

> **설계 미덕**: 거의 모든 외부 연동이 미설정 시 *no-op/폴백*으로 동작 → 로컬에서 일부 기능만으로도 깨지지 않음.

### DB 마이그레이션

```bash
cd backend && alembic upgrade head        # 스키마 적용
alembic revision --autogenerate -m "..."  # 스키마 변경 시
```

---

## 10. TODO 리스트

### 🔴 블로커 — 기능 동작에 필수

- [ ] **타임라인 캐시 키 해시 함수 구현**
  - 위치: `timeline/service.py::compute_commit_set_hash()`
  - 현상: `NotImplementedError` — 타임라인 호출이 실패함
  - 방향: 커밋 해시 목록 정렬 → join → SHA-256

---

### 🔵 컨텍스트 블레임 개선 (신예진)

> end-to-end 동작은 하지만, 정밀 점검 결과 정확성·성능·품질 이슈가 남아 있다.

#### ✅ P0 완료 — 정확성/성능 (2026-06-06)

- [x] **LLM에 실제 diff hunk 누락**
  - `_get_commit_diff`를 `git show -p --stat`으로 수정해 hunk 포함
- [x] **async 이벤트 루프 블로킹**
  - `blame/router.py`의 `context_blame`(async def)이 동기 `service.analyze_blame()`을 직접 호출 → 이벤트 루프 점유
  - `await asyncio.to_thread()`로 스레드 위임
- [x] **프론트 캐시 stale 문제**
  - 프론트 `blameCache` 키가 `filePath:line`이라 파일 편집으로 줄이 밀리면 엉뚱한 줄에 옛 설명 표시
  - `onDidChangeTextDocument` 시 해당 파일의 캐시+핀 전체 무효화

#### 🟡 P1 — 품질·기능 공백

- [ ] **ask_followup 매 질문마다 전부 재계산**
  - 위치: `blame/service.py::ask_followup`
  - 현상: 질문마다 git blame + `_build_context`를 처음부터 다시 함. 직전 analyze_blame의 context 재사용 안 함.
  - 방향: 분석 시 만든 context를 ask 경로가 재사용. 필요 시 Q&A를 DB에 누적.

- [ ] **중복 git 호출**
  - 위치: `blame/service.py:50-51`
  - 현상: router가 이미 구한 branch/ticket을 service가 `get_current_branch`/`extract_ticket`으로 재계산
  - 방향: router가 구한 branch/ticket을 service에 함께 전달

- [ ] **diff 잘라내기 전략 개선**
  - 위치: `blame/service.py::_truncate_diff`
  - 현상: head-only(앞 N자)라 큰 커밋 뒷부분 변경 손실
  - 방향: head+tail 또는 hunk 헤더 우선 보존

- [ ] **관련 변경/PR 범위 한계**
  - 위치: `core/vcs.py` (PR 파일 `per_page=100`), `relatedChanges` 5~6개 캡
  - 현상: 대형 PR에서 핵심 변경 누락 가능
  - 방향: 페이지네이션 또는 "외 N건" 표기

- [ ] **사이드바 내러티브 다듬기**
  - 위치: `contextBlame/sidebar.ts`의 `TODO(개발자 A)`
  - 현상: "3월 15일에"와 설명 사이 연결이 어색, 인용문 없을 때 흐름 부자연스러움

#### 🟢 P2 — 테스트·견고성

- [ ] **블레임 단위 테스트 부재** — 우선 대상:
  - `extract_keywords` (불용어·도메인 우선·중복 제거·순서 보존)
  - `crud.save_blame` / `get_cached_blame` (커밋×파일 dedup 히트/미스)
  - `_build_related_changes` (분류·캡)
  - `vcs._extract_issue_numbers` / `_extract_attachments` 정규식
- [ ] **엣지케이스 견고성** — merge 커밋, detached HEAD, 빈 커밋 메시지, 바이너리 파일

#### 🆕 GitHub Issue 연동 후속 (2026-06-06 전환)

- [ ] **GitLab(MR→Issue) 미지원**
  - 위치: `vcs.find_issues_from_pr_body` — 현재 GitHub host 한정
  - 방향: GitLab MR description의 `Closes #N`도 동일하게 처리
- [ ] **PR 본문 없는 커밋 폴백**
  - 현상: Squash/Rebase 후 PR 본문 없거나 PR 매칭 안 되는 커밋은 issues=[]
  - 방향: 커밋 메시지에서 `#N` 패턴 추출하는 2차 경로
- [ ] **첨부 URL 휴리스틱 정밀화**
  - 현상: PDF/DOCX 확장자 + GitHub user-attachments만 감지, 외부 위키/노션 누락
  - 방향: 도메인 화이트리스트 옵션화

---

### 🟢 선택 — 비용/성능 최적화

- [ ] 역추적 시맨틱 폴백 결과 캐시 (blame처럼 file_id/commit_id 키)
- [ ] 타임라인 map 단계 Bedrock 호출 병렬화 (LangGraph `Send()` API)

---

### 🔧 인프라·보안·배포

- [ ] **`documents` / `document_links` 테이블 및 관련 코드 삭제**
  - ORM: `db/models.py`에서 두 모델 제거
  - 라우터: `features/documents/` 폴더 전체 삭제, `main.py`에서 include_router 제거
  - 백필: `features/onboarding/backfill.py`의 `_link_passages` 등 document_links 참조 정리
  - 마이그레이션: Alembic revision 생성 (`DROP TABLE documents, document_links`)
  - 환경변수: `DOCUMENTS_DIR` 참조 제거
- [ ] CORS `allow_origins=["*"]` → 배포 시 확장 origin으로 제한
- [ ] 에러 응답 표준화 (현재 기능별 `HTTPException(500, f"...: {e}")` 패턴)
- [ ] 백엔드 단위 테스트 추가
- [ ] `backend/Dockerfile` 배포 파이프라인(CI) + 마이그레이션 자동 실행
- [ ] Bedrock 호출 비용/레이트리밋 모니터링

---

### 💡 기능 확장 아이디어

- [ ] 역추적 결과를 사이드바에서 바로 미리보기 (Issue 첨부 PDF 등)
- [ ] 블레임/타임라인/역추적 간 상호 내비게이션 (같은 커밋·티켓으로 연결)
- [ ] 다국어/모노레포·서브모듈 레포 경로 처리

---

### 각자 첫 작업 체크리스트

#### 공통
- [ ] `view.ts`의 TODO를 디자인 시안에 맞게 구현
- [ ] `service.py`의 프롬프트/로직 다듬기

#### 신예진 (Context Blame)
- [ ] 위 "🔵 컨텍스트 블레임 개선" P1부터 착수 (P0 완료)
- [ ] `service.py`의 프롬프트 톤 조정

#### 박성태 (Timeline Summary)
- [ ] 마일스톤 타임라인 시각화 (Webview 추천)
- [ ] `service.py`의 JSON 파싱 실패 시 폴백 처리

#### 전준민 (Requirement Trace)
- [ ] GitHub Issue 첨부 파일 목록을 UI에 보여주는 Webview 구현
- [ ] matchType별 신뢰도 표시 UI

---

## 11. 커밋 메시지 규칙

```
<Type>[Domain]: <Description>

- 상세 내역 1 (선택)
- 상세 내역 2 (선택)
```

### Type 정의

AI 타임라인 요약이 커밋의 성격을 파악하는 기준입니다.

| Type | 설명 | AI 인식 |
| ---- | ---- | ------- |
| `feat` | 새로운 기능 추가 | "기능 추가의 역사" |
| `fix` | 버그 수정 | "디버깅 및 안정화의 역사" |
| `refactor` | 기능 변화 없는 코드 구조 개선 | "구조 개선의 역사" |
| `perf` | 성능 개선 | "성능 최적화의 역사" |
| `docs` | 문서 수정 (README 등) | "설명서 업데이트" |
| `test` | 테스트 추가 / 수정 | (타임라인 집계 제외 가능) |
| `chore` | 자잘한 설정 변경 (패키지 설치 등) | (타임라인 집계 제외 가능) |

### 예시

```
feat[auth]: 로그인 기능 추가
fix[payment]: 결제 금액 계산 오류 수정
refactor[timeline]: 서비스 레이어 분리
perf[blame]: git log 호출 횟수 최적화
docs[readme]: 환경 변수 설명 보완
```

---

## 12. 기여 시 참고

### 기능 추가

- **확장에 기능 추가**: `src/features/<기능>/` 폴더에 캡슐화하고 `src/extension.ts`에 register 호출만 추가.
- **백엔드에 기능 추가**: `features/<기능>/{router,service,schemas}.py` 구성 → `main.py`에 `include_router`.
- **공유 데이터**(commit/file/repo)는 `db/crud_common.py`의 upsert 헬퍼 재사용.
- **스키마 변경**은 ORM 수정 후 Alembic autogenerate.
- **외부 연동 추가** 시 미설정 환경에서 no-op/폴백하도록 작성(로컬 개발 보호).

### 공용 코드 수정 규칙

다음 영역은 세 명이 함께 쓰므로 **PR/팀 합의 후** 수정합니다.

- `src/extension.ts`, `src/shared/**`
- `backend/app/main.py`, `backend/app/core/**`, `backend/app/db/**`
- `package.json`의 `contributes.commands`와 `menus`

새 명령을 추가하거나 응답 스키마를 바꿀 때 외에는 이 영역을 건드릴 일이 거의 없습니다.

---

질문이나 응답 스키마 변경이 필요하면 팀 채널에서 공유해주세요.
