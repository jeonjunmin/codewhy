# CodeWhy 개발 가이드

> 현재 소스 코드(`master` 기준) 기반으로 정리한 **단일 개발 레퍼런스**입니다.
> 개발 현황·구조·담당 분장·커밋 규칙·TODO를 한 곳에서 관리합니다.
> 마지막 정리: 2026-06-20 (역추적 GitHub Issue 전환 완료 §1·§6·§7, 타임라인 SSE 스트리밍 전환 §6, `commit_classifier`/`project` 신설 반영 §5·§7, `documents`/`document_links` 테이블 제거 §7, §10 블로커 해소·TODO 정리)

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

### 요구사항 문서 연결 방식 (현재 상태)

요구사항 문서를 코드와 잇는 방식은 **블레임·역추적이 같은 GitHub Issue 체인으로 통일**되었습니다(2026-06 전환 완료). 타임라인만 문서를 참조하지 않습니다.

| 기능 | 현재 동작 |
|---|---|
| 컨텍스트 블레임 | **GitHub Issue 본문 + 첨부 파일을 실시간 조회**. 별도 업로드 불필요. `commit → PR → Closes #N → Issue → attachments` 체인. |
| 요구사항 역추적 | **GitHub Issue 체인 기반으로 전환 완료.** `commit → PR → Issue → 첨부/코멘트/이벤트`를 실시간 조회. `commit_issues` 테이블이 "커밋↔이슈 번호"만 영구 캐시(cache-aside)하고, 이슈 본문·상태·라벨 등 가변 메타는 조회 시점에 갱신. 매칭 경로는 `issue`/`ticket`/`semantic` 3종(§6). 구 `documents`/`document_links` 저장소는 제거됨(§7). |
| 타임라인 요약 | 요구사항 문서를 직접 참조하지 않음(커밋 메시지/타입만으로 요약). |

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
                                               │  - PR 조회         │   (블레임 + 역추적:
                                               │  - Issue 본문/첨부  │    Issue 본문·첨부·코멘트·
                                               │  - Issue 코멘트/타임라인│    이벤트를 실시간 수집)
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
| AI | AWS Bedrock — Converse API(블레임, boto3 직접), ChatBedrock 스트리밍(타임라인, async generator SSE), Knowledge Base(역추적 시맨틱 검색) |
| VCS 연동 | GitHub REST API (PR·Issue·첨부·코멘트·이벤트 조회), GitLab MR API (블레임 일부 지원) |
| Git | Git CLI subprocess (`app/core/git.py`) |

---

## 5. 디렉터리 구조

```
codewhy/
├── src/                              # VSCode 확장 (TypeScript)
│   ├── extension.ts                  # 진입점 — 기능 register 호출만
│   ├── shared/                       # http 클라이언트, editor/git 유틸, 공용 타입, 로그
│   └── features/
│       ├── contextBlame/             # 신예진 — command / view(webview) / sidebar / api
│       ├── timelineSummary/          # 박성태 — command / api (렌더링은 command 내부)
│       └── requirementTrace/         # 전준민 — command / api (렌더링은 command 내부)
│
└── backend/app/                      # FastAPI 백엔드
    ├── main.py                       # 앱 생성 + 라우터 등록 + DB 연결 확인
    ├── core/                         # 공용 모듈
    │   ├── git.py                    # blame/log/diff/branch 추출
    │   ├── ai_client.py / bedrock.py # Bedrock Converse(boto3) / LangChain ChatBedrock
    │   ├── commit_classifier.py      # 커밋 분류 SSOT — classify_commit/SKIP_TYPES/filter_meaningful (블레임·타임라인 공유)
    │   ├── config.py                 # pydantic-settings 환경설정
    │   ├── tickets.py                # 커밋/파일명에서 티켓(PAY-2041)·이슈 번호(#N) 추출
    │   ├── vcs.py                    # GitHub/GitLab PR·Issue·첨부·코멘트·이벤트 조회 (lru_cache)
    │   ├── knowledge_base.py         # Bedrock Knowledge Base 조회 (역추적 semantic, RAG retrieve)
    │   └── doc_index.py              # 업로드 문서 S3→KB 인덱싱 (온보딩 시맨틱, 미설정 시 no-op)
    ├── db/
    │   ├── models.py                 # 통합 스키마 ORM (8테이블)
    │   ├── postgres.py               # async engine / get_db / Base
    │   └── crud_common.py            # repo/commit/file 공유 백본 upsert
    ├── ai/timeline_file_graph.py     # 타임라인 Bedrock 스트리밍 (async generator, ChatGPT식 토큰 yield)
    ├── alembic/versions/             # DB 마이그레이션
    └── features/
        ├── blame/                    # 신예진 — router/service/crud/schemas
        ├── timeline/                 # 박성태 — router/service/crud/schemas
        ├── traceability/             # 전준민 — router/service/crud/schemas (commit_issues 캐시)
        ├── project/                  # router/schemas — 프로젝트 초기화 + 저장된 타임라인 일괄 조회 (/api/v1/project)
        └── onboarding/               # router/backfill/schemas (브라운필드 백본 백필)
```

---

## 6. 기능 현황

### ✅ 컨텍스트 블레임 (Context Blame) — 동작 가능

`POST /api/blame/context`, `POST /api/blame/reason`, `POST /api/blame/ask`

**무엇을 하나**: 사용자가 클릭한 코드 라인의 "왜 바꿨는지"를 AI가 추론해 사이드바에 보여준다.

- git blame → 커밋 해석 → PR에서 연결된 GitHub Issue + 첨부 문서 수집
- 코드 + 커밋 + Issue 맥락을 Bedrock Converse로 종합 → **변경 사유 한국어 설명**
- 부가: 티켓/팀 매핑, 같은 티켓 후속 커밋 → "함께 일어난 일" 조립
- **라인 수정 이력 + 이슈 롤업**: 사이드바 하단에 해당 라인을 거쳐 간 커밋 이력을 표시하고, 각 행에 참조 이슈 수 배지(`issueCount`)와 실제 이동 링크(`issueUrl`)를 제공 (#24/#33/#46)
- **커밋별 사유 펼침**(`/reason`): 이력 항목을 펼치면 그 커밋의 변경 사유를 지연 생성. `/context`와 같은 `(file_id, commit_id)` 캐시를 공유하므로 재호출 없이 적중
- "AI에게 더 묻기"(`/ask`) 후속 질문 지원
- **캐시**: `blame_explanations` UNIQUE(**file_id, commit_id**) — 커밋×파일 단위. 커밋↔이슈 매핑은 `commit_issues` 영구 캐시 공유(역추적과 동일 백본)
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
3. 노이즈 커밋(test/chore/docs)은 LLM 호출을 건너뛰고 정형 응답으로 대체 — **적용 완료** (`commit_classifier.classify_commit` → `SKIP_TYPES` 시 `_noise_response`, 타임라인과 처리 원칙 #2 정렬)

### ✅ 타임라인 요약 (Timeline Summary) — 동작 가능

`POST /api/timeline/summary` (캐시 적중 시 JSON, 미스 시 **SSE 스트림**)
`GET /api/v1/project/timeline` (DB에 저장된 요약 일괄 조회 — Bedrock 0회)

- **Bedrock 토큰 스트리밍** — `app/ai/timeline_file_graph.py`가 LangGraph StateGraph 대신 **async generator**로 직접 Bedrock을 호출해 ChatGPT식으로 토큰을 실시간 yield. 캐시 미스 시 라우터가 `text/event-stream`으로 흘려보낸다.
- 노이즈 커밋(test/chore/docs)을 `commit_classifier.filter_meaningful`로 제거 → 캐시 키·LLM 입력 양쪽에서 제외
- JSON 파싱 실패 시 누적 원본 텍스트를 `summary`로 폴백(`parse_ai_response`)
- **캐시**: `timeline_summaries` UNIQUE(file_id, commit_set_hash) — `compute_commit_set_hash`는 의미 있는 커밋 해시를 정렬해 SHA-256
- 프론트엔드가 스트리밍 토큰을 실시간 표시하고, 종료 프레임의 `{summary, milestones}`로 마일스톤 카드 확정
- **프로젝트 일괄 조회**: `project` 기능이 `GET /api/v1/project/timeline`으로 저장된 모든 파일 요약을 한 번에 반환(Bedrock 미호출). `POST /initialize`는 일괄 분석을 폐지하고 ACK만 반환(lazy on-demand 정착)

#### 구현 흐름 상세

> 핵심 설계 원칙: **요약 단위는 파일이고, 캐시 키는 그 파일의 "의미 있는 커밋 집합"이다.**
> 노이즈 커밋(test/chore/docs)은 캐시 키 계산과 LLM 호출 양쪽에서 모두 제외한다. 같은 분류 기준을 블레임이 import해 쓰므로, 정의는 `backend/app/core/commit_classifier.py` 단일 소스에 둔다.

```
사용자가 파일 타임라인 열기 (VSCode 확장)
    │
    ▼
[1] git log --follow  →  파일 커밋 이력 수집 (확장에서 수행)
    │
    ▼
[2] POST /api/timeline/summary { repoPath, filePath, commits }
    │
    ▼
[3] 공유 백본 upsert  →  repo/file/commit/commit_files (db/crud_common, 멱등)
    │
    ▼
[4] 캐시 키 계산 (노이즈 면제 적용)
        target  = commit_classifier.filter_meaningful(commits)
        keyhash = compute_commit_set_hash = SHA-256(sorted(target.hash))
    │
    ▼
[5] 캐시 조회  →  timeline_summaries WHERE (file_id, commit_set_hash=keyhash)
        (service.prepare_summary)
    │
    ├─ 적중 ──▶  JSON(TimelineResponse) 즉시 반환  (Bedrock 0회)
    │
    └─ 미스 ──▶  StreamingResponse(text/event-stream) — service.stream_summary
            [6] git diff 추출 → stream_file_summary(async generator)
                  ① 대표 커밋 타입/도메인으로 관점 프롬프트 구성
                  ② ChatBedrock 스트리밍 호출 — 토큰을 `data: {"delta": "..."}`
                     프레임으로 즉시 yield (단일 호출, map-reduce 없음)
                  ③ 스트림 종료 → 누적 텍스트 parse_ai_response(JSON|폴백)
                  ④ 마지막 `data: {"done": true, "summary":..., "milestones":...}`
            [7] timeline_summaries upsert (file_id, commit_set_hash, summary, milestones)
            [8] 프론트가 누적 토큰 표시 → done 프레임으로 마일스톤 카드 확정
```

**Bedrock 호출 횟수 (캐시 미스 1회 분석 기준)**: 파일당 **스트리밍 1회**. (이전 map-reduce의 `C+1`회에서 단일 호출로 단순화됨. 큰 파일 토큰 가드는 입력 커밋 텍스트 구성 단계에서 처리.)

**캐시 무효화 정책**:
- 의미 있는 커밋(feat/fix/refactor/perf/…)이 추가/변경되면 `keyhash` 가 바뀌어 자동 재분석.
- 노이즈 커밋(test/chore/docs)만 푸시된 경우 `filter_meaningful`이 그것을 제외 → `keyhash` 불변 → **재분석 안 일어남**(블레임의 §6 공통 처리 원칙 #2 정렬).

**LLM 진입점**:
- `app.core.bedrock.get_bedrock_llm()` → `langchain_aws.ChatBedrock` 인스턴스를 async generator에서 스트리밍 호출(`HumanMessage` + 토큰 스트림).
- 블레임이 쓰는 `app.core.ai_client.call_bedrock`(boto3 Converse 직접)과는 다른 진입점. 같은 Bedrock 모델을 호출하지만 SDK 레이어가 다르며, 프롬프트 캐싱(`cachePoint`)은 타임라인 경로에서 미사용 — 파일마다 본문이 달라 캐싱 이득이 작기 때문.

**비용 최적화**:
1. **Lazy on-demand** — 사용자가 실제로 연 파일만 분석. `project/initialize`의 일괄 분석은 폐지됨
2. **노이즈 면제 캐시 키** — 의미 없는 커밋이 캐시를 깨지 않음
3. **스트리밍 단일 호출** — 체감 지연을 토큰 단위로 분산, map-reduce 다중 호출 제거
4. **저장 후 재조회 무비용** — `GET /project/timeline`은 DB만 읽어 Bedrock 0회

### ✅ 요구사항 역추적 (Requirement Trace) — 동작 가능 (GitHub Issue 체인 기반)

`POST /api/trace/requirement`

코드(파일 단위)에서 연관 GitHub Issue와 그 첨부·코멘트·이벤트를 찾아 보여주는 기능. **블레임과 동일한 `commit → PR → Issue` 체인으로 통일**되었습니다(구 `documents`/`document_links` 저장소 폐지). 커밋↔이슈 번호는 `commit_issues` 테이블에 **영구 캐시(cache-aside)** 하고, 이슈 본문·상태·라벨 등 가변 메타는 조회 시점에 GitHub에서 갱신합니다.

**추적 경로 (실제 코드)**:

```
파일(+blamed 커밋들) → 각 커밋
    → ① PR 본문에서 Issue 직접 연결 (Closes #N)        → link_source="issue"  (확정)
    → ② 커밋/브랜치 티켓 번호로 GitHub Issue 검색         → link_source="ticket" (높음)
    → ③ 커밋 메시지 키워드로 Issue/Bedrock KB 시맨틱 검색  → link_source="semantic" (추정)
    → 미인덱싱 커밋만 GitHub 조회 → commit_issues 저장(증분)
    → 이슈 번호 집합으로 본문/상태/라벨/코멘트/첨부 일괄 refresh
    → matchType/confidence 와 함께 UI에 표시
```

| matchType | 방식 | 확신도 |
|-----------|------|--------|
| `issue` | 커밋 → PR 본문 → Issue 직접 연결 (첨부 있음) | 확정 |
| `ticket` | 커밋/브랜치 티켓 번호(PAY-2041) → GitHub Issue 검색 | 높음 |
| `semantic` | 커밋 메시지 키워드 → GitHub Issue / Bedrock Knowledge Base 검색 | 추정 (낮음) |

**응답 구성**(`traceability/schemas.py`): 이슈별 `DocumentMatch`(title/url/issueNumber/state/labels/assignee/excerpt) + 첨부 `AttachmentMatch`(label/url/pageCount) + 코멘트·이벤트 `CommentMatch`(comment: author/body/createdAt | event: labeled/assigned/closed 등). 이슈 상세를 코멘트·타임라인까지 펼쳐 보여줍니다(#46).

> **외부 연동 미설정 시** 빈 결과로 폴백 → 로컬에서 절대 깨지지 않습니다. semantic 경로의 Bedrock KB(`core/knowledge_base.py`)는 KB 미설정 시 빈 리스트를 돌려줍니다.

### 🧪 브라운필드 온보딩 (Onboarding) — 개발 중

`POST /api/onboarding/backfill`

- 레거시 레포 전체 git 히스토리를 훑어 **공유 백본(commits/files/commit_files)** 을 일괄 upsert
- GitHub Issue 실시간 조회로 전환한 이후 `document_links` 사전 생성은 폐지 — 커밋↔이슈는 파일 조회 시점에 `commit_issues`로 증분 인덱싱
- UNIQUE 제약으로 재실행 중복 방지(idempotent)

---

### 🔁 공통 처리 원칙 (블레임 ↔ 타임라인)

같은 프로젝트의 두 기능이 비용·속도 정책에서 어긋나면 운영 일관성이 깨지고 회귀가 누적된다. 두 기능 모두 아래 다섯 원칙을 따른다. 새 기능을 추가할 때도 이 원칙에 정렬해 설계한다.

| # | 원칙 | 블레임 | 타임라인 |
|---|------|-------|---------|
| 1 | **Lazy on-demand** — 사용자 액션 시점에만 분석, 일괄 prefetch 금지 | ✅ 적용 (라인 클릭 1회) | ✅ 적용 (`project/initialize` 일괄 분석 폐지, 파일 열 때 1회) |
| 2 | **노이즈 커밋 LLM 우회** — test/chore/docs는 LLM 호출·캐시 무효화에서 모두 제외 | ✅ 적용 (`analyze_blame` 진입 분기) | ✅ 적용 (`filter_meaningful` → 캐시 키·LLM 입력 제외) |
| 3 | **공유 백본** — repo/commit/file upsert는 항상 `db/crud_common.py` 경유 | ✅ | ✅ |
| 4 | **외부 API 메모이즈** — GitHub PR·Issue 조회는 요청 스코프 캐시로 중복 호출 차단 | ✅ 적용 (`vcs.py` lru_cache 128) | — (외부 API 미사용) |
| 5 | **폴백 정책 일관** — 외부 의존성 미설정·실패 시 예외 대신 동등 형식의 폴백 응답 | ✅ `[Bedrock 미연동] …` | ✅ `parse_ai_response` 원본 텍스트 폴백 |

원칙 2를 위한 **공통 커밋 분류기**(`backend/app/core/commit_classifier.py`)는 **신설 완료** — `classify_commit`/`SKIP_TYPES`/`filter_meaningful`을 단일 소스로 두고, 타임라인(캐시 키·LLM 입력)과 블레임(노이즈 우회)이 모두 import해 같은 기준으로 동작한다.

#### LLM 호출 방식 비교 (Bedrock + LangChain)

같은 AWS Bedrock 모델을 부르지만 진입점·구조·캐싱 활용이 다르다. 이 차이는 도메인 요구에서 비롯한 것이며, 통일이 아니라 **이해**가 목적이다.

##### 한 눈에 — "직통 전화" vs "콜센터 시스템"

```
블레임                              타임라인
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  service.py                          timeline_file_graph.py (async generator)
      │                                     │
      │  boto3로 직접 호출                    │  LangChain ChatBedrock 스트리밍에 위임
      ▼                                     ▼
  [AWS Bedrock]                   [LangChain ChatBedrock.astream]
                                          │  (토큰을 즉시 yield)
                                          ▼
                                    [AWS Bedrock]

  "직통 전화"                        "스트리밍 받아쓰기"
```

##### 블레임 — boto3 직접 호출 (`core/ai_client.py`)

```python
def call_bedrock(prompt, context=None, cache=False):
    content = [{"text": context}]
    content.append({"cachePoint": {"type": "default"}})  # ← 핵심: 프롬프트 캐싱 제어
    content.append({"text": prompt})
    boto3_client.converse(messages=[{"role": "user", "content": content}])
```

`cachePoint`를 메시지 중간에 삽입하는 세부 구조를 직접 제어해야 **프롬프트 캐싱**이 작동한다.
LangChain이 이 구조를 추상화해버리면 캐싱 제어권이 없어진다.

```
요청 1: [context(1000토큰), cachePoint, prompt] → 전체 비용
요청 2: [context(캐시 적중!),  cachePoint, prompt] → context 비용 0
                                                       ↑ 이것 때문에 직접 호출
```

##### 타임라인 — LangChain ChatBedrock 스트리밍 (`core/bedrock.py` + `ai/timeline_file_graph.py`)

```python
# core/bedrock.py
def get_bedrock_llm():
    return ChatBedrock(...)        # LangChain 객체 반환

# ai/timeline_file_graph.py — async generator 안에서
async def stream_file_summary(...):
    llm = get_bedrock_llm()
    async for chunk in llm.astream([HumanMessage(content=prompt)]):
        yield chunk.content        # 토큰을 즉시 SSE 프레임으로
```

> **이전 구조 변경 메모**: 과거에는 LangGraph `StateGraph`(classify→map→reduce→parse→retry) map-reduce 파이프라인이었으나, ChatGPT식 실시간 출력 요구로 **단일 스트리밍 호출 + async generator**로 단순화됨. `langgraph` 의존성은 `requirements.txt`에 남아 있으나 현재 코드에서 미사용(§10 정리 대상).

```
stream_file_summary (단일 노드)
      │
      ▼
ChatBedrock.astream() ─── 토큰 스트림 → `data: {"delta": "..."}` 프레임
      │
      ▼
스트림 종료 → parse_ai_response(JSON|폴백) → `data: {"done": true, ...}`
```

##### 비교 요약

| 측면 | 블레임 | 타임라인 |
|---|---|---|
| 진입점 | `core/ai_client.py::call_bedrock` | `core/bedrock.py::get_bedrock_llm` |
| SDK 레이어 | boto3 **Converse API 직접** 호출 | **LangChain `ChatBedrock`** 인스턴스 (`.astream`) |
| 오케스트레이션 | 단순 순차 (설명 1회 → 제안 1회) | **async generator 스트리밍** (단일 호출, 토큰 즉시 yield) |
| 메시지 구성 | `[context, cachePoint, prompt]` 3파트 | `HumanMessage(prompt)` 단일 |
| **프롬프트 캐싱** | ✅ 활용 — 같은 context 블록 두 번 보내 캐시 적중 | ❌ 미활용 — 파일마다 본문이 달라 효과 작음 |
| 호출 횟수/요청 | 2회 고정 (설명 + 제안) | **1회 스트리밍** (캐시 미스 시) |
| 토큰 가드 | `_MAX_DIFF_CHARS=2000` head-only 잘라내기 | 입력 커밋 텍스트 구성 단계에서 제한 |
| 응답 전달 | JSON 일괄 반환 | **SSE(`text/event-stream`)** 토큰 스트림 + done 프레임 |
| 폴백 | `[Bedrock 미연동] …` 메시지 | `parse_ai_response` — JSON 실패 시 원본 텍스트를 summary로 |

**왜 두 진입점인가**: 블레임은 "프롬프트 캐싱으로 비용 깎기"가 우선이라 Converse를 직접 부르는 게 유리하고, 타임라인은 "토큰을 받는 즉시 화면에 흘려보내는 실시간 UX"가 우선이라 LangChain `ChatBedrock`의 `.astream`이 적합하다. 두 진입점을 한 SDK로 합치려면 한쪽 도메인 요구를 양보해야 하므로 현재는 **공존을 인정**한다. 단, `core/commit_classifier.py`처럼 **공통 데이터/규칙은 공유 모듈**로 끌어내는 정책은 유지한다.

---

## 7. 데이터 모델 (통합 스키마 8테이블)

```
repositories ─┬─ commits ─┬─ commit_files ─ files
              │           ├─ commit_issues          (역추적·블레임 이슈 캐시)
              │           │
  blame_explanations ─────┘         timeline_summaries

timeline_summary_cache  (repo_path/file_path 단위 분석 상태 — project 기능)

(구 documents / document_links 테이블은 GitHub Issue 전환으로 제거됨)
```

| 테이블 | 역할 | 핵심 제약 |
| --- | --- | --- |
| `repositories` | 레포 식별자 루트 | identifier UNIQUE |
| `commits` | git 커밋 (세 기능 공유) | UNIQUE(repo_id, commit_hash) |
| `files` | 레포 내 파일 경로 | UNIQUE(repo_id, file_path) |
| `commit_files` | 커밋↔파일 N:M + 변경량 | (commit_id, file_id) PK |
| `blame_explanations` | 블레임 AI 결과 캐시 | UNIQUE(file_id, commit_id) |
| `timeline_summaries` | 타임라인 요약 캐시 | UNIQUE(file_id, commit_set_hash) |
| `commit_issues` | 커밋↔GitHub Issue 번호 영구 캐시 (cache-aside, 역추적·블레임 공유) | UNIQUE(commit_id, issue_number) |
| `timeline_summary_cache` | 파일별 마지막 분석 상태(repo_path, file_path) — `project` 기능 | UNIQUE(repo_path, file_path) |

> **`documents` / `document_links` 테이블 — 제거 완료 (2026-06 GitHub Issue 전환)**
> 요구사항 문서를 별도 업로드·저장하던 방식에서 **GitHub Issue 첨부/코멘트를 실시간 조회**하는 방식으로 전환하면서 두 테이블과 ORM(`Document`/`DocumentLink`)을 제거했습니다.
> - 문서 메타데이터·커밋↔문서 매핑을 DB에 저장하지 않음 — `commit → PR → Issue → attachments` 체인으로 파생
> - 변하지 않는 "커밋↔이슈 번호"만 `commit_issues`에 영구 캐시하고, 가변 메타는 조회 시점 refresh
> - `features/documents/` 데드코드 폴더도 삭제 완료 (2026-06-20).

**설계 원칙**:
- 세 기능이 공유하는 데이터(작성자·날짜·메시지·티켓·이슈)는 `commits`/`files`/`commit_issues`에 한 번만 저장하고, 기능별 산출물은 FK로 참조.
- 스키마 변경은 **반드시 Alembic autogenerate 마이그레이션**으로.

---

## 8. API 엔드포인트 요약

> 프론트엔드 `src/shared/types.ts`와 백엔드 `features/<name>/schemas.py`의 키 이름이 **일치**해야 합니다.
> 응답 스키마를 바꾸려면 양쪽을 동시에 수정하세요.

| Method | Path | 담당 | 요청 | 응답 |
| --- | --- | --- | --- | --- |
| GET | `/health` | — | — | 헬스체크 |
| POST | `/api/blame/context` | 신예진 | `{filePath, line, repoPath, ...}` | `{explanation, commitHash, author, date, lineIssues, ...}` |
| POST | `/api/blame/reason` | 신예진 | `{filePath, hash, commit, followups, ...}` | `{reason}` (라인 이력 항목 펼침 — 커밋별 사유) |
| POST | `/api/blame/ask` | 신예진 | `{filePath, line, repoPath, question}` | `{answer}` |
| POST | `/api/timeline/summary` | 박성태 | `{filePath, repoPath, commits}` | 캐시 적중 → JSON `{summary, milestones}` / 미스 → **SSE** `data:{delta}` … `data:{done, summary, milestones}` |
| POST | `/api/trace/requirement` | 전준민 | `{filePath, repoPath, commits, branch, remoteUrl}` | `{documents:[{title, url, matchType, confidence?, issueNumber?, state?, labels, assignee?, attachments[], comments[]}]}` |
| POST | `/api/onboarding/backfill` | — | `{repoPath, since?, limit?}` | 레포 전체 공유 백본 백필 |
| POST | `/api/v1/project/initialize` | 박성태 | `{project_path}` | `{status:"READY"}` (lazy ACK, 일괄 분석 없음) |
| GET | `/api/v1/project/timeline` | 박성태 | `?project_path=` | 저장된 파일별 타임라인 일괄 (Bedrock 0회) |
| GET | `/api/v1/project/status` | 박성태 | `?project_path=` | `{analyzed_files, status}` |

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
| `GITHUB_TOKEN` | GitHub PR·Issue·첨부·코멘트 조회 (블레임·역추적) | PR/Issue 연동 생략 |
| `GITLAB_TOKEN` | GitLab MR→Issue 조회 (블레임 일부) | MR 연동 생략 |
| `BEDROCK_KNOWLEDGE_BASE_ID` | 역추적 semantic 검색용 KB ID | RAG 생략 (semantic 빈 결과) |
| `BEDROCK_KB_MAX_RESULTS` | KB 조회 결과 수 | 기본 4 |
| `DOC_INDEX_S3_BUCKET` / `DOC_INDEX_S3_PREFIX` | 온보딩 문서 KB 인덱싱용 S3 위치 | 인덱싱 no-op |
| `CODEWHY_TEAM_MAP` | 작성자→팀 매핑 JSON 경로 | team 칸 생략 |
| `CODEWHY_ATTACHMENT_DOMAINS` | 첨부로 인정할 외부 도메인 화이트리스트 (쉼표 구분, 예: `notion.so,confluence.atlassian.com`) | 확장자/사용자업로드 휴리스틱만 사용 |

> **설계 미덕**: 거의 모든 외부 연동이 미설정 시 *no-op/폴백*으로 동작 → 로컬에서 일부 기능만으로도 깨지지 않음.

### DB 마이그레이션

```bash
cd backend && alembic upgrade head        # 스키마 적용
alembic revision --autogenerate -m "..."  # 스키마 변경 시
```

---

## 10. TODO 리스트

### ✅ 블로커 해소 — 역추적 아키텍처 갈래 결정 (2026-06)

- [x] **역추적을 GitHub Issue 체인으로 전환 (선택지 A 채택·구현 완료)**
  - 역추적이 블레임과 동일한 `commit → PR → Issue` 체인을 사용. matchType `issue`/`ticket`/`semantic` 신설.
  - `documents`/`document_links` 테이블·ORM 제거, `commit_issues` 영구 캐시 신설, 응답 스키마 `title`/`url` 기반으로 전환.
  - `features/documents/` 데드코드 폴더 삭제 완료 (2026-06-20).

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

- [x] **'라인 수정 이력' 이슈 배지 → 실제 이슈 이동 링크 연결** (#24/#33/#46)
  - 사이드바 '라인 수정 이력' 각 행에 참조 이슈 수 배지(`issueCount`)와 실제 이동 링크(`issueUrl`) 연결 완료.
  - 커밋별 사유 펼침(`POST /api/blame/reason`) 추가 — `/context`와 같은 `(file_id, commit_id)` 캐시 공유.
  - 커밋↔이슈 매핑은 역추적과 같은 `commit_issues` 백본을 공유.

- [x] **ask_followup 매 질문마다 전부 재계산** (이미 적용됨)
  - `service.py`의 `_CONTEXT_CACHE`(LRU 100건)로 `analyze_blame`이 만든 context를 `ask_followup`이 재사용. 캐시 미스 시에만 재빌드.

- [ ] **ask_followup Q&A DB 누적 (보류, UX 결정 대기)**
  - 보류 이유: 비용 측면은 `_CONTEXT_CACHE`로 해결됨. DB 누적은 "다른 세션/차원에서 이어 보기" UX가 정해질 때 의미. 그 전엔 작업량 대비 가치 작음.
  - 진행 시 범위: Alembic revision + `blame_qa(file_id, commit_id, question, answer, created_at)` 신규 테이블 + 사이드바 스레드 히스토리 노출.

- [x] **중복 git 호출** (2026-06-07)
  - router가 구한 `info`/`branch`/`ticket`을 `analyze_blame`에 그대로 전달. router 내부에서도 `ticket`을 한 번만 계산해 캐시 키와 service 인자에 재사용.

- [x] **diff 잘라내기 전략 개선** (이미 적용됨)
  - `_truncate_diff`가 `head + "\n…(중략)…\n" + tail` 전략으로 양끝 보존. hunk 헤더 우선 보존은 §10 선택(`diff 토큰 관리 전략 통일`)에서 다룸.

- [x] **관련 변경/PR 범위 한계** (2026-06-07)
  - `_github_pr_files` 페이지네이션(최대 3페이지/300건). `_build_related_changes`가 캡(5/6) 도달 시 "외 N개 파일"·"외 N개 커밋" 한 줄을 카드 형태로 추가해 잔여 변경 가시화.

- [x] **사이드바 내러티브 다듬기** (2026-06-07)
  - `formatNarrative`를 메타("작성자 · 날짜")와 본문(`explanation`) 두 줄로 분리. `decorate()`에 `\n → <br/>` 변환 추가. 빈 explanation 폴백 포함.

- [x] **노이즈 커밋 LLM 우회 (타임라인과 처리 원칙 정렬)** (2026-06-07)
  - 적용: `blame/service.py::analyze_blame` 진입 직후 `commit_classifier.classify_commit` 호출 → `SKIP_TYPES` 해당 시 `_noise_response`로 즉시 반환. Bedrock·GitHub API 0회.
  - 결과: §6 공통 처리 원칙 #2 정렬 완료

- [ ] **노이즈 응답 문구 확정 (UX)**
  - 위치: `blame/service.py::_build_noise_explanation`
  - 현상: 임시 폴백 문구(`[자동 분류] {label} 정비 커밋입니다 — "{quote}"`)로 동작 중. 사이드바 톤과 일관성 미점검.
  - 결정 사항: 사이드바 톤(존댓말 / 따옴표 종류 / 분류 라벨 표현) 정해 5~10줄 본문 확정
  - 후보 패턴:
    - `"[자동 분류] {label} 정비 커밋입니다 — \"{quote}\""` (현재 임시값)
    - `"이 줄은 \"{quote}\" 커밋의 일부로, {label} 변경이라 별도 분석을 건너뛰었습니다."`
  - 입력 변수: `label`(`_NOISE_LABELS`: 문서/테스트/설정/잡무), `quote`(커밋 메시지 첫 줄, 폴백 `(커밋 메시지 없음)`)

- [x] **GitHub PR/Issue 조회 메모이즈** (2026-06-07)
  - 적용: `core/vcs.py`에 저수준 헬퍼 4개(`_github_pr_listing_for_commit`/`_github_pr_detail`/`_github_pr_files`/`_github_issue`) 분리 + `@lru_cache(maxsize=128)`. 인자는 `(base, owner, repo, …)` str/int 조합으로 hashable.
  - 무효화: 프로세스 재시작 시 자연 만료. PR 본문·Issue 본문은 빈번히 바뀌지 않으므로 충분.
  - 결과: §6 공통 처리 원칙 #4 정렬 완료

#### 🟢 P2 — 테스트·견고성

- [x] **블레임 단위 테스트 추가** (2026-06-07)
  - `backend/tests/blame/` 신설(38건, 전부 PASS): `test_extract_keywords` / `test_related_changes` / `test_vcs_regex` / `test_commit_classifier`(SSOT 보호) / `test_truncate_diff`(hunk 보존) / `test_to_response`(crud mapping). `pytest>=8.0.0`을 `requirements.txt`에 추가.
  - 남은 항목: `crud.save_blame`/`get_cached_blame`의 ON CONFLICT 통합 테스트 — PostgreSQL testcontainer 도입 시점에 별도 진행(아래 신규 TODO).

- [ ] **crud DB 통합 테스트 (Postgres testcontainer)**
  - 위치: `backend/tests/blame/test_crud_db.py` (예정)
  - 의도: `save_blame`의 `ON CONFLICT DO UPDATE`와 `get_cached_blame`의 커밋×파일 dedup 적중을 실제 PostgreSQL 로 검증. `JSONB` 필드 round-trip 도 포함.
  - 의존성: `testcontainers-python` 또는 `docker-compose -f tests/docker-compose.yml`. 도입 시점에 함께 작성.

- [x] **엣지케이스 견고성** (2026-06-07)
  - `_get_commit_diff`에 `-m --first-parent` 추가 (merge 커밋 일반 diff 강제)
  - `get_current_branch`는 이미 detached HEAD 시 빈 문자열 반환
  - `_build_context`가 빈 message/diff/author/date 각각 폴백 라벨로 LLM 환각 방지
  - `_get_commit_numstat`은 바이너리 `-` 를 `0`으로 처리 (기존)

#### 🆕 GitHub Issue 연동 후속 (2026-06-06 전환 / 2026-06-07 완료)

- [x] **GitLab(MR→Issue) 지원** (2026-06-07)
  - `find_issues_from_pr_body`의 host 가드 제거 + `_fetch_issues`가 GitHub/GitLab 분기. `_gitlab_issue` 헬퍼(lru_cache 128).
- [x] **PR 본문 없는 커밋 폴백** (2026-06-07)
  - `find_issues_from_commit_message` 신설 + `_safe_find_issues`가 PR 본문에서 매칭이 없으면 커밋 메시지 `#N` 으로 2차 폴백.
- [x] **첨부 URL 휴리스틱 도메인 화이트리스트** (2026-06-07)
  - `CODEWHY_ATTACHMENT_DOMAINS` 환경변수(쉼표 구분)로 Notion/Confluence/위키 등 확장자 없는 외부 링크를 첨부로 인정. `config.get_attachment_domain_allowlist`.

---

### 🟢 선택 — 비용/성능 최적화

- [ ] 역추적 시맨틱(KB) 결과 캐시 (`commit_issues`처럼 영구 캐시 검토)
- [x] ~~타임라인 map 단계 병렬화~~ — map-reduce 폐지·스트리밍 단일 호출 전환으로 무의미해짐
- [ ] `langgraph` 의존성 정리 — `requirements.txt`에 남아 있으나 현재 코드 미사용
- [x] **diff 토큰 관리 전략 통일 (블레임 측)** (2026-06-07)
  - `_truncate_diff`가 hunk 헤더(`@@ ... @@`) 우선 보존 — 통째로 살릴 수 있는 hunk 는 살리고, 잘린 hunk 들의 헤더만 `[잘린 hunks — 헤더만 보존]` 블록으로 끝에 모아 LLM 이 어느 영역이 잘렸는지 인지하게 함. patch 가 아니면 head+tail 폴백.
  - 타임라인 청크 전략 정렬은 별도 plan(타임라인 map 청크 사이즈 정책) 후속.

---

### 🔧 인프라·보안·배포

- [x] **공통 커밋 분류기 추출 (블레임/타임라인 공유)** — 완료
  - `backend/app/core/commit_classifier.py` 신설: `classify_commit`(정규식 `^(\w+)(?:\[([^\]]+)\])?:\s*(.+)$`), `SKIP_TYPES = {"test","chore","docs"}`, `filter_meaningful`
  - 사용처: 타임라인 `compute_commit_set_hash`·`filter_meaningful`, 블레임 `analyze_blame` 노이즈 우회
  - 효과: §6 공통 처리 원칙 #2 정렬 완료

- [x] **`documents` / `document_links` 데드코드 삭제 — 완료 (2026-06-20)**
  - `db/models.py`의 `Document`/`DocumentLink` ORM 제거, 역추적 서비스 GitHub Issue 체인(`commit_issues`)으로 재설계, `backfill`의 `_link_passages` 제거(공유 백본 upsert만 수행), `main.py`의 `documents_router` 미연결.
  - `features/documents/` 폴더(라우터 `/search`·`/download` + service/schemas) 삭제 — `python -c "import app.main"` 정상 확인.
  - 남은 점검: `config.py`의 `DOCUMENTS_DIR`/`get_documents_dir`·`doc_index`(KB 인덱싱) 등 업로드 문서 경로 참조가 더 필요한지 확인 후 정리.
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
- [x] 마일스톤 타임라인 시각화 + 스트리밍 토큰 실시간 표시 — `src/features/timelineSummary/`(렌더링은 command 내부)
- [x] SSE 스트리밍 전환 — `ai/timeline_file_graph.py::stream_file_summary`(async generator), 라우터 `text/event-stream`
- [x] JSON 파싱 실패 폴백 — `parse_ai_response`가 원본 텍스트를 summary로 폴백
- [x] 프로젝트 일괄 조회 — `GET /api/v1/project/timeline`(DB만, Bedrock 0회)

#### 전준민 (Requirement Trace)
- [x] **선결 해소**: 역추적 GitHub Issue 체인 전환 완료 (선택지 A)
- [x] GitHub Issue 첨부·코멘트·이벤트를 UI에 표시 — `AttachmentMatch`/`CommentMatch` 스키마 + 이슈 상세 펼침(#46)
- [~] matchType별 신뢰도 표시 UI — `src/features/requirementTrace/command.ts`(렌더링 통합)에 배지+% 표시. 상세 뷰 다듬기만 남음
- [ ] **`backendUrl` 설정이 역추적 패널에 즉시 반영되지 않음**
  - 현상: `command.ts`가 `codewhy.backendUrl`을 읽어 webview HTML에 문자열로 구워 넣음(`const BACKEND = '${backendUrl}'`, [command.ts:149]). 블레임·타임라인은 매 요청마다 `createHttpClient()`로 다시 읽어 즉시 반영되는 반면, 역추적은 **이미 열린 패널이 옛 URL을 그대로 사용**하고 명령을 다시 실행해 패널을 새로 열어야 새 값이 적용됨.
  - 개선: backendUrl을 HTML에 굽지 말고 `panel.webview.postMessage`로 전달하거나, fetch를 확장(extension) 측에서 대행(`createHttpClient` 경유)하도록 변경 → 다른 두 기능과 "항상 최신 설정값" 동작 통일.

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
