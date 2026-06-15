# CodeWhy 개발 가이드

> 현재 소스 코드(`master` 기준) 기반으로 정리한 **단일 개발 레퍼런스**입니다.
> 개발 현황·구조·담당 분장·커밋 규칙·TODO를 한 곳에서 관리합니다.
> 마지막 정리: 2026-06-15 (소스 재분석 전체 최신화 — ① 블레임·타임라인 SSE 스트리밍 반영,
> ② 타임라인 LangGraph map-reduce → 단일 스트리밍 호출(`ai/timeline_file_graph.py`)로 교체,
> ③ 역추적 GitHub Issue 전환 완료(§10 블로커 해소), ④ 통합 패널(3탭) 프론트 구조 반영,
> ⑤ `commit_classifier` 신설·`documents` ORM 제거 반영, ⑥ 잔존/깨진 코드 TODO 갱신)

---

## 목차

| # | 섹션 | 한 줄 요약 |
|---|------|-----------|
| 1 | [프로젝트 개요](#1-프로젝트-개요) | CodeWhy가 뭔지, 3대 기능 |
| 2 | [담당 분장](#2-담당-분장) | 기능별 폴더 소유권 |
| 3 | [아키텍처](#3-아키텍처) | 확장 ↔ 백엔드 ↔ 외부 시스템 |
| 4 | [기술 스택](#4-기술-스택) | TS, FastAPI, PostgreSQL, Bedrock, SSE |
| 5 | [디렉터리 구조](#5-디렉터리-구조) | 폴더 트리 |
| 6 | [기능 현황](#6-기능-현황) | 각 기능 완성도와 동작 흐름 |
| 7 | [데이터 모델](#7-데이터-모델-통합-스키마) | 통합 스키마 ERD |
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
| 요구사항 역추적 (Requirement Trace) | 코드 → PR → GitHub Issue(첨부 기획 문서)를 연결해 보여줌 | 전준민 |

추가로 **브라운필드 온보딩**(레거시 레포 일괄 적재) 기능이 개발 중입니다.

### 요구사항 문서 연결 방식 (현재 상태)

> **2026-06-15 정리: 세 기능 모두 "GitHub Issue 실시간 조회"로 통일되었습니다.**
> 과거 가이드에 있던 "역추적은 Document 저장소 기반" 분기는 **해소되었습니다** —
> 역추적도 블레임과 동일하게 `commit → PR → Closes #N → Issue → attachments` 체인을
> 요청 시점에 실시간 조회합니다(§10 블로커 #1 해소, 선택지 A 채택).

| 기능 | 현재 동작 |
|---|---|
| 컨텍스트 블레임 | GitHub Issue 본문 + 첨부 파일을 실시간 조회. `commit → PR → Closes #N → Issue → attachments` 체인. |
| 요구사항 역추적 | **블레임과 동일한 GitHub Issue 실시간 조회.** `vcs.find_issues_for_commit`로 issue/ticket/semantic 매칭. `documents`/`document_links` 테이블 의존 제거됨. |
| 타임라인 요약 | 요구사항 문서를 직접 참조하지 않음(커밋 메시지/타입/diff만으로 요약). |

---

## 2. 담당 분장

세 명이 동시에 작업해도 충돌이 나지 않도록 **기능별 폴더 단위로 코드 소유권을 분리**했습니다.
각 개발자는 원칙적으로 **자기 기능 폴더 안에서만** 파일을 만들고 고칩니다.

| 담당 | 기능 | 프론트엔드 | 백엔드 |
| ---- | ---- | ---------- | ------ |
| 신예진 | Context Blame | `src/features/contextBlame/` (통합 패널 호스트) | `backend/app/features/blame/` |
| 박성태 | Timeline Summary | `src/features/timelineSummary/` (명령 등록) | `backend/app/features/timeline/` + `backend/app/ai/timeline_file_graph.py` |
| 전준민 | Requirement Trace | `src/features/requirementTrace/` (명령 등록) | `backend/app/features/traceability/` |

> ⚠️ **프론트 UI 통합**: 타임라인·역추적은 더 이상 독립 Webview 가 아니라 **컨텍스트 블레임이
> 호스팅하는 통합 패널의 탭**(타임라인/이슈)으로 표시됩니다. 두 기능의 `index.ts`는
> 명령을 등록해 `contextBlame/view.ts`의 `runTimelineTab()`/`runIssueTab()`을 호출만 합니다.
> 따라서 타임라인·역추적 폴더에는 더 이상 `view.ts`가 없습니다(§5 참고).

---

## 3. 아키텍처

```
┌──────────────────────┐   HTTP(axios) + SSE   ┌──────────────────────────┐
│  VSCode 확장 (TS)     │ ───────────────────▶ │  FastAPI 백엔드 (Python)   │
│  src/                │                       │  backend/app/             │
│  - 우클릭 명령 3종     │ ◀─────────────────── │  - /api/blame             │
│  - 통합 패널(3탭)      │  JSON  또는           │  - /api/timeline          │
│  - CodeLens / Hover   │  text/event-stream   │  - /api/trace             │
└──────────────────────┘  (델타 토큰 스트림)    │  - /api/onboarding        │
                                               │  - /api/v1/project        │
                                               └───────────┬──────────────┘
                                                           │
                          ┌────────────────────────────────┼─────────────────────────┐
                          ▼                                ▼                           ▼
                  ┌───────────────┐              ┌──────────────────┐        ┌──────────────────┐
                  │  Git CLI       │              │ PostgreSQL (RDS) │        │  AWS Bedrock      │
                  │  (subprocess)  │              │  통합 스키마       │        │  - Converse(boto3)│
                  │  blame/log/diff│              │  + 캐시           │        │  - ChatBedrock    │
                  └───────────────┘              └──────────────────┘        │    (astream)      │
                                                                             └──────────────────┘
                                               ┌──────────────────┐                   │
                                               │  GitHub / GitLab  │ ◀────────────────┘
                                               │  - PR/MR 조회      │   (블레임·역추적:
                                               │  - Issue 본문/첨부  │    Issue 본문·첨부를
                                               └──────────────────┘    LLM/UI 맥락으로 전달)
```

**데이터 흐름의 공통 패턴 (블레임 기준)**:

1. 확장이 로컬 git 정보(repoPath/filePath/line 등)를 백엔드로 전송
2. 백엔드가 git CLI로 blamed 커밋 추출 → 공유 백본(repo/file/commit) upsert
3. PostgreSQL 캐시 조회(커밋×파일 단위) — **적중 시 JSON 즉시 반환**
4. 캐시 미스 시 분기:
   - **노이즈 커밋**(test/chore/docs) → Bedrock·GitHub 호출 없이 정형 JSON 응답
   - **의미있는 커밋** → GitHub Issue 수집 후 **SSE 스트림**으로 설명 토큰 실시간 전달
5. 스트림 종료 시점에 누적 결과를 캐시 후 응답 확정

> **듀얼 모드(JSON ↔ SSE)**: 블레임·타임라인 모두 캐시 적중/노이즈는 `application/json`,
> 의미있는 캐시 미스는 `text/event-stream`(SSE)으로 응답합니다. 프런트는 응답 `Content-Type`으로
> 두 경로를 구분합니다.

---

## 4. 기술 스택

| 영역 | 기술 |
| --- | --- |
| VSCode 확장 | TypeScript 5.9, VSCode Extension API (`^1.118.0`), axios (스트림 응답 직접 순회) |
| 백엔드 | Python 3.11+, FastAPI, Uvicorn, Pydantic v2 / pydantic-settings |
| 실시간 전송 | SSE(`text/event-stream`) — `data: {...}\n\n` 프레임(meta/delta/done) |
| DB | PostgreSQL(RDS), SQLAlchemy 2.0(async/asyncpg), Alembic(psycopg2) |
| AI | AWS Bedrock — boto3 Converse(블레임 동기·캐싱) + `langchain_aws.ChatBedrock`(스트리밍 `astream`) |
| 오케스트레이션 | **LangGraph `StateGraph`** — 블레임/역추적 파이프라인(조건 분기·병렬 fan-out/fan-in·폴백). 스트리밍은 `astream_events`(`ai/blame_graph.py`, `ai/trace_graph.py`) |
| VCS 연동 | GitHub REST API (PR·Issue·첨부), GitLab MR/Issue API |
| Git | Git CLI subprocess (`app/core/git.py`) |
| 의존성(주요) | `langgraph`, `langchain-aws`, `langchain-core`, `anthropic`, `langchain-anthropic`, `pypdf`, `pytest` (`backend/requirements.txt`) |

> ℹ️ **참고**: **타임라인**은 LangGraph 없이 `ai/timeline_file_graph.py`의 단일 async 스트리밍 호출을
> 쓴다(ChatGPT식 출력 요구). **블레임·역추적**은 LangGraph `StateGraph`로 오케스트레이션하되,
> `astream_events`로 토큰 스트리밍을 그대로 유지한다(§6).

---

## 5. 디렉터리 구조

```
codewhy/
├── src/                              # VSCode 확장 (TypeScript)
│   ├── extension.ts                  # 진입점 — register 호출 + 워크스페이스 감시(커밋/저장 트리거)
│   ├── shared/                       # http 클라이언트, editor 유틸, 공용 타입, 로그
│   └── features/
│       ├── contextBlame/             # 신예진 — 통합 패널 호스트
│       │   ├── index.ts              #   명령 + CodeLens/Hover 등록
│       │   ├── command.ts            #   블레임 실행 진입
│       │   ├── view.ts               #   통합 패널(3탭) + CodeLens + HoverProvider
│       │   ├── sidebar.ts            #   패널 HTML/탭 렌더링(블레임/타임라인/이슈)
│       │   └── api.ts                #   /api/blame/context(스트림) · /ask
│       ├── timelineSummary/          # 박성태 — index.ts(명령) + api.ts(스트림 호출). view 없음(통합 패널 탭)
│       └── requirementTrace/         # 전준민 — index.ts(명령) + command.ts + api.ts. view 없음(통합 패널 탭)
│
└── backend/app/                      # FastAPI 백엔드
    ├── main.py                       # 앱 생성 + 라우터 5종 등록 + DB 연결 확인
    ├── core/                         # 공용 모듈
    │   ├── git.py                    # blame/log/diff/branch/line-history 추출
    │   ├── ai_client.py              # boto3 Converse 직접 호출(call_bedrock, 프롬프트 캐싱)
    │   ├── bedrock.py                # langchain_aws.ChatBedrock 팩토리(get_bedrock_llm)
    │   ├── commit_classifier.py      # ★ 공통 커밋 분류기(classify_commit/SKIP_TYPES/filter_meaningful)
    │   ├── config.py                 # pydantic-settings 환경설정
    │   ├── tickets.py                # 커밋/브랜치에서 티켓(PAY-2041) 추출
    │   ├── vcs.py                    # GitHub/GitLab PR·MR·Issue·첨부 조회(+lru_cache)
    │   ├── knowledge_base.py         # (온보딩) Bedrock Knowledge Base 조회 — semantic 폴백
    │   └── doc_index.py              # (온보딩) S3 업로드 + KB ingestion 트리거
    ├── db/
    │   ├── models.py                 # 통합 스키마 ORM (7테이블)
    │   ├── postgres.py               # async engine / get_db / Base / AsyncSessionLocal
    │   └── crud_common.py            # repo/commit/file 공유 백본 upsert
    ├── ai/
    │   ├── blame_graph.py            # ★ 블레임 LangGraph StateGraph (병렬·조건분기·스트리밍) + stream_blame_graph
    │   ├── trace_graph.py            # ★ 역추적 LangGraph 폴백 체인(issue→ticket→semantic) + atrace
    │   └── timeline_file_graph.py    # ★ 파일별 타임라인 — Bedrock 단일 스트리밍 호출 + JSON 파싱(LangGraph 미사용)
    ├── alembic/versions/             # DB 마이그레이션(0001~0005, merge head 포함)
    └── features/
        ├── blame/                    # 신예진 — router/service/crud/schemas (SSE 스트리밍)
        ├── timeline/                 # 박성태 — router/service/crud/schemas (SSE 스트리밍)
        ├── traceability/             # 전준민 — router/service/schemas (GitHub Issue 기반)
        ├── project/                  # router/schemas — /api/v1/project (initialize/timeline/status)
        ├── onboarding/               # router/backfill/schemas (브라운필드 백필)
        └── documents/                # ⚠️ 라우터/서비스 잔존하나 main.py 에 미등록(고아 코드, §10)
```

---

## 6. 기능 현황

### ✅ 컨텍스트 블레임 (Context Blame) — 동작 가능 (SSE 스트리밍)

`POST /api/blame/context`, `POST /api/blame/ask`

**무엇을 하나**: 사용자가 클릭한 코드 라인의 "왜 바꿨는지"를 AI가 추론해 통합 패널 블레임 탭에 보여준다.

- git blame → 커밋 해석 → PR에서 연결된 GitHub/GitLab Issue + 첨부 문서 수집
- 코드 + 커밋 + Issue 맥락을 Bedrock으로 종합 → **변경 사유 한국어 설명**(스트리밍 타이핑)
- 부가: 티켓/팀 매핑, 같은 티켓 후속 커밋 → "함께 일어난 일"(`relatedChanges`) 조립
- "라인 수정 이력" 목록 + 커밋별 '이슈 N' 배지(커밋 메시지 `#N` 개수, GitHub API 0회)
- "AI에게 더 묻기"(`/ask`) 후속 질문 지원 — `_CONTEXT_CACHE`(최대 100건)로 맥락 재사용
- **캐시**: `blame_explanations` UNIQUE(**file_id, commit_id**) — 커밋×파일 단위
- Bedrock 미설정/실패 시 원인별 degraded 문구로 폴백(`_degraded_explanation`) → 캐싱 건너뜀(자동 회복)
- 프론트엔드 부가: **CodeLens('🔍 왜 바꿨어?')** + **Hover 팝업**(분석된 라인 위), 핀 고정(`blame.pin`)

#### 구현 흐름 상세 (라우터 듀얼 모드)

> 핵심 설계 원칙: **"왜 바뀌었나"는 줄(line)이 아니라 커밋이 그 파일에 가한 변경의 속성이다.**
> 줄 번호는 `git blame`으로 커밋을 찾기 위한 포인터일 뿐이므로, 분석·저장 단위를 **커밋×파일**로 잡는다.

```
사용자가 라인 클릭 (CodeLens/명령/Hover)
    │
    ▼
[0] git blame  →  blamed commit_hash 해석 (실패 시 uncommitted_response 단락)
    │
    ▼
[1] 공유 백본 upsert  →  repo/file/commit/commit_files 행 확보 (멱등)
    │
    ▼
[2] 캐시 조회  →  blame_explanations WHERE (file_id, commit_id)
    │
    ├─ 적중 ──▶  application/json 으로 저장된 설명 즉시 반환  (Bedrock 0회)
    │
    └─ 미스 ──▶  분기:
            ├─ 노이즈 커밋(test/chore/docs)  →  application/json
            │     run_blame_graph(ainvoke) → classify → noise_response 노드 (Bedrock·GitHub 0회)
            │
            └─ 의미있는 커밋  →  text/event-stream(SSE)
                  ai/blame_graph.stream_blame_graph 가 LangGraph StateGraph 를
                  astream_events 로 돌리며 3프레임 전달:
                    ① meta  — git 만으로 즉시 구하는 메타/라인 이력 (그래프 진입 전 인라인)
                    ② delta — explain 노드의 LLM 토큰(on_chat_model_stream) 실시간
                    ③ done  — assemble 노드 결과(relatedChanges/출처/첨부 등) 확정
                  스트림 종료 시 degraded 가 아니면 blame_explanations upsert
```

> **LangGraph 오케스트레이션**: 미스(의미있는 커밋) 경로는 `ai/blame_graph.py`의 `StateGraph`로 구성된다.
> `classify`에서 노이즈/의미있는 커밋을 **조건 분기**하고, `fetch_github`(PR+이슈)와 `fetch_followups`를
> **병렬 fan-out** → `build_context`에서 **fan-in(1회)**. (PR과 이슈를 별도 super-step으로 쪼개면 두 부모가
> 서로 다른 step에 끝나 `build_context`/`explain`이 2회 실행 → Bedrock 중복 호출되므로, GitHub 조회를 한 노드로
> 묶어 같은 super-step에 끝나게 했다.) `explain` 실패는 try/except 대신 degraded 상태로 `assemble`에 합류.
> 노드는 전부 `features/blame/service.py` 헬퍼를 재사용한다(로직 재작성 없음).

**캐시 무효화**: 줄이 새 커밋으로 수정되면 `git blame`이 다른 commit_hash를 반환 → 캐시 키 불일치 → 자동 재분석. TTL 없음.

**비용 최적화**:
1. diff 길이 제한 (`_MAX_DIFF_CHARS = 2000`) — `_truncate_diff`가 **hunk 헤더 우선 보존**(잘린 hunk 헤더만 끝에 모음)
2. 노이즈 커밋(test/chore/docs)은 Bedrock·GitHub 호출을 건너뛰고 정형 응답으로 대체 — **적용됨**(`commit_classifier` 공유)
3. GitHub PR/Issue 조회 메모이즈(`vcs.py` `@lru_cache(128)`)
4. 후속 질문은 `_CONTEXT_CACHE`로 맥락 재사용(git/PR/Issue 재조회 차단)
5. 동기 경로(`ask`/노이즈는 아님)에서 boto3 Converse `cachePoint` 프롬프트 캐싱 활용

### ✅ 타임라인 요약 (Timeline Summary) — 동작 가능 (SSE 스트리밍)

`POST /api/timeline/summary`

> ⚠️ **아키텍처 변경(2026-06-15 반영)**: 과거 가이드의 **LangGraph Map-Reduce StateGraph**
> (classify_and_split → map → reduce → parse → retry/fallback 5노드)는 **더 이상 사용되지 않습니다.**
> ChatGPT 류 실시간 출력 요구에 맞춰 **`ai/timeline_file_graph.py`의 단일 async 스트리밍 호출**
> (`stream_file_summary`, `ChatBedrock.astream`)로 교체되었습니다. 청크 분할·map/reduce·재시도 노드는 제거됨.

- 파일 커밋 이력 + 최신 커밋 diff(`git show HEAD -- file`)를 한 번에 Bedrock에 넣어 스트리밍 요약
- 응답은 `{summary, milestones[]}` JSON — 스트림 종료 후 `parse_ai_response`가 누적 텍스트를 파싱(실패 시 raw를 summary로 폴백)
- 커밋 `type`(feat/fix/refactor/perf/…)에 따라 관점 레이블(`_TYPE_PERSPECTIVE`)을 프롬프트에 주입
- **캐시**: `timeline_summaries` UNIQUE(file_id, commit_set_hash). `compute_commit_set_hash`는
  `filter_meaningful`로 **노이즈 커밋을 제외한** 정렬 해시의 SHA-256
- **Lazy on-demand**: 일괄 prefetch 폐지. `/api/v1/project/initialize`는 즉시 `READY` ACK만 반환하고,
  사용자가 실제로 연 파일만 `/summary` 시점에 분석 (구 일괄 분석 모듈 `timeline/tasks.py`·`ai/graph.py` 등은 제거됨)
- 통합 패널 '타임라인' 탭에서 마일스톤 시각화(세로선·날짜 칩·설명 카드)

#### 구현 흐름 상세

> 핵심 설계 원칙: **요약 단위는 파일이고, 캐시 키는 그 파일의 "의미 있는 커밋 집합"이다.**
> 노이즈 커밋(test/chore/docs)은 캐시 키 계산에서 제외한다(`commit_classifier.filter_meaningful`).
> 블레임과 같은 분류기를 공유하므로 정의는 `core/commit_classifier.py` 단일 소스에 둔다.

```
사용자가 파일 타임라인 열기 (통합 패널 '타임라인' 탭)
    │
    ▼
[1] 확장이 git log 로 파일 커밋 이력 수집 → POST /api/timeline/summary {repoPath, filePath, commits}
    │
    ▼
[2] service.prepare_summary
        ① crud.upsert_commits  → 공유 백본 upsert + 파일 전체 이력 조회
        ② compute_commit_set_hash(filter_meaningful(stored))  → SHA-256 캐시 키
        ③ get_cached_summary(file_id, set_hash)
    │
    ├─ 적중 ──▶  application/json (TimelineResponse) 즉시 반환  (Bedrock 0회)
    │
    └─ 미스 ──▶  text/event-stream(SSE)  service.stream_summary
            ① git show HEAD -- file 로 diff 추출 (없으면 커밋 목록 텍스트 폴백)
            ② stream_file_summary(ChatBedrock.astream)  → delta 프레임 실시간
            ③ 스트림 종료 → parse_ai_response(누적) → {summary, milestones}
            ④ crud.save_summary(file_id, set_hash, result)
            ⑤ done 프레임으로 최종 결과 확정
```

**캐시 무효화 정책**:
- 의미 있는 커밋(feat/fix/refactor/perf/…)이 추가/변경되면 `set_hash`가 바뀌어 자동 재분석.
- 노이즈 커밋(test/chore/docs)만 추가된 경우 `filter_meaningful`이 제외 → `set_hash` 불변 → **재분석 안 함**.

### ✅ 요구사항 역추적 (Requirement Trace) — 동작 가능 (GitHub Issue 기반)

`POST /api/trace/requirement`

> **2026-06-15: Document 저장소 의존 제거 완료.** 코드 라인 → blamed 커밋 → PR → GitHub Issue →
> 첨부 파일을 **요청 시점에 실시간 조회**합니다(`vcs.find_issues_for_commit`). DB(`documents`/`document_links`)
> 의존 없음. 응답은 통합 패널 '이슈' 탭에 신뢰도와 함께 표시됩니다.

**추적 경로 (실제 코드)**:

```
코드 라인 → git blame → commit
    → vcs.find_issues_for_commit(repo, hash, message)
        · issue    : PR 본문 → Closes #N → Issue 직접 연결 (첨부 있음)
        · ticket   : 커밋 메시지 티켓 번호(PAY-2041)로 Issue 매칭
        · semantic : 커밋 키워드로 관련 Issue 검색
    → 각 Issue/첨부를 DocumentMatch(title/url/matchType/confidence/excerpt)로 변환
```

| matchType | 방식 | confidence |
|-----------|------|--------|
| `issue` | PR → Issue 직접 연결, 첨부 파일 있음 | 확정 (None) |
| `ticket` | 커밋 메시지 티켓 번호 → Issue 매칭 | 0.8 |
| `semantic` | 커밋 키워드 → 관련 Issue 검색 | 0.5 |

> 응답 스키마: `{documents: [{title, url, matchType, confidence?, excerpt?}]}`
> (과거의 `name`/`downloadUrl` → `title`/`url`로 변경됨 — 프론트 `DocumentMatch` 타입과 일치.)

> **LangGraph 오케스트레이션**: 3단 폴백 체인을 `ai/trace_graph.py`의 `StateGraph`로 구성한다.
> `try_issue → [발견 시 종료 / 없으면] try_ticket → [...] try_semantic → format_results`를 **조건부 엣지**로
> 표현해, 순차 try/except보다 "왜 이 matchType이 나왔는가"가 명확하다(Mermaid 시각화). 라우터는
> `trace_graph.atrace(...)`를 호출하며, 각 노드는 `vcs`/`traceability.service._format_results`를 재사용한다.
> 스트리밍 불필요 → `ainvoke`.

### 🧪 브라운필드 온보딩 (Onboarding) — 개발 중 (일부 깨짐)

`POST /api/onboarding/backfill`

- 레거시 레포 전체 git 히스토리를 훑어 commits/files/commit_files 공유 백본에 사전 적재
- GitHub Issue 전환 이후 `document_links` 사전 생성은 폐지 — `run_backfill`은 `linksCreated: 0` 고정
- ⚠️ **현재 라우터-서비스 시그니처 불일치(깨짐)**: `onboarding/router.py`가
  `run_backfill(..., min_confidence=...)`와 `doc_index.is_enabled()`를 호출하지만,
  `backfill.run_backfill`은 `min_confidence` 인자를 받지 않고 `doc_index`에 `is_enabled()`가 없어
  **호출 시 예외**가 납니다(§10 블로커).

### 🗂 프로젝트 초기화/조회 (`/api/v1/project`)

| 엔드포인트 | 동작 |
|---|---|
| `POST /initialize` | lazy on-demand 전환으로 일괄 분석 트리거 없이 `{"status":"READY"}` ACK만 반환 |
| `GET  /timeline?project_path=` | DB에 저장된 타임라인 요약을 즉시 반환(Bedrock 0회). repo→files→summaries 3단 조회 |
| `GET  /status?project_path=` | 분석 완료 파일 수 반환 |

> 확장(`extension.ts`)이 워크스페이스 오픈/커밋 감지(`.git/COMMIT_EDITMSG`)/저장 디바운스(30초) 시
> `/initialize`를 호출하지만, 현재 백엔드는 ACK만 하므로 실제 분석은 사용자가 파일을 열 때 일어납니다.

---

### 🔁 공통 처리 원칙 (블레임 ↔ 타임라인)

같은 프로젝트의 두 기능이 비용·속도 정책에서 어긋나면 운영 일관성이 깨지고 회귀가 누적된다. 두 기능 모두 아래 원칙을 따른다. 새 기능을 추가할 때도 이 원칙에 정렬해 설계한다.

| # | 원칙 | 블레임 | 타임라인 |
|---|------|-------|---------|
| 1 | **Lazy on-demand** — 사용자 액션 시점에만 분석, 일괄 prefetch 금지 | ✅ (라인 클릭 1회) | ✅ (`/initialize`는 ACK만, `/summary`에서 lazy 분석) |
| 2 | **노이즈 커밋 우회** — test/chore/docs는 LLM 호출·캐시 무효화에서 제외 | ✅ (`blame_graph` classify 분기) | ✅ (`compute_commit_set_hash`의 `filter_meaningful`) |
| 3 | **공유 커밋 분류기** — 노이즈 판정은 `core/commit_classifier.py` 단일 소스 | ✅ import | ✅ import |
| 4 | **공유 백본** — repo/commit/file upsert는 항상 `db/crud_common.py` 경유 | ✅ | ✅ |
| 5 | **외부 API 메모이즈** — GitHub PR·Issue 조회는 `vcs.py` `lru_cache(128)` | ✅ | — (외부 API 미사용) |
| 6 | **SSE 듀얼 모드** — 캐시 적중/노이즈는 JSON, 의미있는 미스는 스트림 | ✅ `stream_blame_graph` | ✅ `stream_summary` |
| 7 | **폴백 일관** — 외부 의존성 미설정·실패 시 예외 대신 동등 형식의 폴백 응답 | ✅ `_degraded_explanation` | ✅ `parse_ai_response` raw 폴백 |

#### LLM 호출 방식 비교 (Bedrock + LangChain)

같은 AWS Bedrock 모델을 부르지만 진입점·구조가 경로마다 다르다. 통일이 아니라 **이해**가 목적이다.

| 측면 | 블레임 동기 경로 (`/ask`, 노이즈 외) | 블레임/타임라인 스트리밍 경로 |
|---|---|---|
| 진입점 | `core/ai_client.py::call_bedrock` | `core/bedrock.py::get_bedrock_llm` |
| SDK 레이어 | boto3 **Converse API 직접** 호출 | **`langchain_aws.ChatBedrock`** 인스턴스 |
| 호출 방식 | `converse(messages=[...])` 동기 | `.astream([HumanMessage])` async 토큰 스트림 |
| 메시지 구성 | `[context, cachePoint, prompt]` 3파트 | `SystemMessage` + `HumanMessage(context+instruction)` |
| **프롬프트 캐싱** | ✅ 활용 — `cachePoint`로 context 블록 캐시 적중 | ❌ 미활용 — 스트리밍은 1회성 본문이라 공유 프리픽스 없음 |
| 쓰임 | 후속 질문(`ask_followup`) 등 비스트리밍 호출 | 블레임 설명 스트리밍(`blame_graph` explain 노드), 타임라인(`stream_file_summary`) |

**왜 두 진입점인가**: boto3 직접 호출은 `cachePoint`를 메시지 중간에 삽입해 **프롬프트 캐싱**을
세밀하게 제어할 수 있고(비스트리밍 반복 호출에 유리), LangChain `ChatBedrock`은 `astream`으로
**토큰 단위 실시간 출력**을 간결하게 얻는다(SSE 스트리밍에 유리). 두 도메인 요구가 다르므로
**공존을 인정**하되, 커밋 분류 같은 **공통 규칙은 `core/commit_classifier.py`로 끌어내는 정책**은 유지한다.

---

## 7. 데이터 모델 (통합 스키마)

ORM(`db/models.py`) 기준 **활성 7테이블**. 과거의 `documents`/`document_links`는 ORM에서 제거되었습니다
(역추적 GitHub Issue 전환). 단, 마이그레이션으로 생성된 물리 테이블은 DROP 전까지 DB에 남아 있습니다(§10).

```
repositories ─┬─ commits ─┬─ commit_files ─ files
              │           │
  blame_explanations ─────┘         timeline_summaries

timeline_summary_cache   (repo_path, file_path) 단위 보조 캐시

[DB 물리 잔존, ORM 제거됨] documents / document_links   (§10에서 DROP 예정)
```

| 테이블 | 역할 | 핵심 제약 |
| --- | --- | --- |
| `repositories` | 레포 식별자 루트 | identifier UNIQUE |
| `commits` | git 커밋 (블레임·타임라인 공유) | UNIQUE(repo_id, commit_hash) |
| `files` | 레포 내 파일 경로 | UNIQUE(repo_id, file_path) |
| `commit_files` | 커밋↔파일 N:M + 변경량 | (commit_id, file_id) PK |
| `blame_explanations` | 블레임 AI 결과 캐시 | UNIQUE(file_id, commit_id) |
| `timeline_summaries` | 타임라인 요약 캐시 | UNIQUE(file_id, commit_set_hash) |
| `timeline_summary_cache` | 파일별 마지막 분석 데이터 보조 캐시 | UNIQUE(repo_path, file_path) |

**설계 원칙**:
- 세 기능이 공유하는 데이터(작성자·날짜·메시지·티켓)는 `commits`/`files`에 한 번만 저장하고, 기능별 산출물은 FK로 참조.
- 스키마 변경은 **반드시 Alembic autogenerate 마이그레이션**으로.

> **마이그레이션 현황**: `0001_init_schema` → `0002_backfill_trace` → `0003_blame_commit_grain` /
> `0003_documents_file_data`(분기) → `0004_blame_issue_attachments` → `0005_merge_heads_…`(merge head).
> `documents`/`document_links`를 DROP하는 마이그레이션은 아직 없습니다.

---

## 8. API 엔드포인트 요약

> 프론트엔드 `src/shared/types.ts`와 백엔드 `features/<name>/schemas.py`의 키 이름이 **일치**해야 합니다.
> 응답 스키마를 바꾸려면 양쪽을 동시에 수정하세요.

| Method | Path | 담당 | 요청 | 응답 |
| --- | --- | --- | --- | --- |
| GET | `/health` | — | — | `{status:"ok"}` |
| POST | `/api/blame/context` | 신예진 | `{filePath, line, repoPath}` | **JSON 또는 SSE** — `{explanation, commitHash, author, date, ticket?, team?, sourceRef?, issueUrl?, attachments?, changeStats?, prInfo?, relatedChanges?, lineHistory?, aiDegraded?}` |
| POST | `/api/blame/ask` | 신예진 | `{filePath, line, repoPath, question}` | `{answer}` |
| POST | `/api/timeline/summary` | 박성태 | `{filePath, repoPath, commits[]}` | **JSON 또는 SSE** — `{summary, milestones:[{date, description}]}` |
| POST | `/api/trace/requirement` | 전준민 | `{filePath, line, repoPath}` | `{documents:[{title, url, matchType?, confidence?, excerpt?}]}` |
| POST | `/api/onboarding/backfill` | — | `{repoPath, since?, limit?, confidenceThreshold?}` | `{commitsScanned, commitsMatched, linksCreated, indexConfigured}` (⚠️ 현재 깨짐, §10) |
| POST | `/api/v1/project/initialize` | — | `{project_path}` | `{status:"READY"}` (ACK only) |
| GET | `/api/v1/project/timeline` | — | `?project_path=` | `TimelineItem[]` (DB 저장 요약 즉시 반환) |
| GET | `/api/v1/project/status` | — | `?project_path=` | `{project_path, analyzed_files, status}` |

> **SSE 응답 프레임**: `data: {"meta":{...}}` → `data: {"delta":"토큰"}` … →
> `data: {"done":true, ...최종필드}`. 오류 시 `data: {"error":"..."}`. 프런트는 `Content-Type`이
> `text/event-stream`이면 프레임을 누적, `application/json`이면 단일 결과로 처리합니다.

> ℹ️ `features/documents/`의 `/search`·`/{id}/download`는 코드가 존재하나 `main.py`에 **미등록**이라
> 라우팅되지 않습니다(고아 코드, §10).

---

## 9. 로컬 개발 환경

### 사전 요구사항

- VSCode 1.118.0+, Node.js 18+, Python 3.11+, Git 2.25+
- PostgreSQL (로컬 또는 RDS), 선택적으로 AWS Bedrock 자격증명

### 설치 & 실행

```bash
npm install
cp backend/.env.example backend/.env      # 값 채우기

npm run backend:install                    # pip install -r backend/requirements.txt (최초 1회)
npm run backend:dev                        # uvicorn app.main:app --reload --port 8000 --app-dir backend

npm run watch                              # 다른 터미널: TS 감시 빌드
# VSCode F5 → Extension Development Host
```

`/health`가 200을 반환하면 백엔드 준비 완료.

### 주요 환경변수 (`backend/.env`)

| 키 | 용도 | 미설정 시 |
| --- | --- | --- |
| `DATABASE_URL` | PostgreSQL 접속(드라이버는 런타임/Alembic이 자동 정규화) | localhost 기본값 |
| `AWS_ACCESS_KEY_ID/SECRET/SESSION_TOKEN` | Bedrock 자격증명 | `~/.aws` 폴백 |
| `AWS_DEFAULT_REGION` | Bedrock 리전 | `ap-northeast-2` |
| `BEDROCK_MODEL_ID` | LLM 모델 ID | Claude 3.5 Sonnet v2 |
| `ANTHROPIC_API_KEY` | (선택) Anthropic 직접 호출 | 미사용 |
| `GITHUB_TOKEN` | GitHub PR·Issue·첨부 조회 | PR/Issue 연동 생략 |
| `GITLAB_TOKEN` | GitLab MR·Issue 조회 | MR 연동 생략 |
| `CODEWHY_TEAM_MAP` | 작성자→팀 매핑 JSON 경로 | team 칸 생략 |
| `CODEWHY_ATTACHMENT_DOMAINS` | 첨부로 인정할 외부 도메인 화이트리스트(쉼표 구분) | 확장자/업로드 휴리스틱만 사용 |
| `BEDROCK_KNOWLEDGE_BASE_ID` | (온보딩 semantic) KB ID | RAG 생략 |
| `DOC_INDEX_S3_BUCKET` / `DOC_INDEX_S3_PREFIX` | (온보딩) 문서 인덱싱 S3 | 시맨틱 인덱싱 no-op |
| `BEDROCK_KB_DATA_SOURCE_ID` | (온보딩) KB ingestion 데이터소스 | 자동 ingestion 생략 |
| `DOCUMENTS_DIR` | (잔존) 업로드 문서 보관 디렉터리 | `./uploaded_documents` |

> **설계 미덕**: 거의 모든 외부 연동이 미설정 시 *no-op/폴백*으로 동작 → 로컬에서 일부 기능만으로도 깨지지 않음.

### 확장 설정 (`package.json` contributes.configuration)

| 키 | 기본값 | 용도 |
|---|---|---|
| `codewhy.codeLens.enabled` | `true` | 에디터 라인에 '🔍 왜 바꿨어?' CodeLens 표시 |
| `codewhy.hover.enabled` | `true` | 분석된 라인 Hover 시 블레임 요약 팝업 |
| `codewhy.backendUrl` | `http://localhost:8000` | (고급) 자체 호스팅 백엔드 URL |

### DB 마이그레이션

```bash
cd backend && alembic upgrade head        # 스키마 적용
alembic revision --autogenerate -m "..."  # 스키마 변경 시
```

---

## 10. TODO 리스트

### 🔴 블로커 — 정리/수정 필요

- [x] **역추적 아키텍처 갈래 결정 (해소됨, 2026-06-15)**
  - 선택지 A(GitHub Issue 첨부 실시간 조회) 채택 완료. `traceability/service.py`가 `vcs.find_issues_for_commit`로
    issue/ticket/semantic 매칭, 응답 스키마 `title`/`url`로 조정됨. `documents`/`document_links` ORM 제거됨.
- [ ] **온보딩 backfill 엔드포인트 깨짐 (시그니처 불일치)**
  - 현상: `onboarding/router.py`가 `run_backfill(..., min_confidence=req.confidenceThreshold)`와
    `doc_index.is_enabled()`를 호출하지만, `backfill.run_backfill`은 `min_confidence`를 받지 않고
    `doc_index`에 `is_enabled()`가 없어 **호출 즉시 예외**.
  - 수정 방향: (A) 라우터에서 `min_confidence`/`doc_index` 참조 제거하고 `run_backfill` 현 시그니처
    (`since`/`limit`)에 맞춤 + `indexConfigured`를 다른 값(또는 고정 False)으로, 또는
    (B) GitHub Issue 전환에 맞춰 온보딩을 "백본 적재 전용"으로 단순화하고 `BackfillResponse`에서
    문서/인덱스 관련 필드 의미를 재정의.

---

### 🟢 LangGraph 오케스트레이션 (해커톤)

- [x] **블레임 StateGraph** (`ai/blame_graph.py`) — 조건 분기(노이즈)·병렬 fan-out(`fetch_github` ∥ `fetch_followups`)·
  fan-in·degraded 합류. `stream_blame_graph`가 `astream_events`로 SSE 3프레임 스트리밍 유지. 라우터는 1줄 교체.
- [x] **역추적 StateGraph** (`ai/trace_graph.py`) — issue→ticket→semantic 폴백 체인을 조건부 엣지로. 라우터 `atrace` 경유.
- [x] **fan-in 중복 실행 버그 수정** — PR/이슈를 별도 노드로 두면 `build_context`/`explain`이 2회 실행돼 Bedrock이
  중복 호출되던 문제를, GitHub 조회를 한 노드(`fetch_github`)로 묶어 해결. `test_blame_graph_stream`가 `explain` 1회 실행을 회귀 검증.
- [x] **Mermaid 시각화** — `python -m app.ai.blame_graph` / `trace_graph`. README에 다이어그램 게재.
- [x] **테스트** — `tests/blame/test_blame_graph_{routing,stream}.py`, `tests/trace/test_trace_graph_fallback.py` (47건 전부 통과).
- [x] **단일 파이프라인 통합** — 라우터 노이즈 경로를 `run_blame_graph`(=`blame_graph.ainvoke`)로 전환.
  스트리밍/비스트리밍이 같은 그래프를 공유한다. 사문화된 `service.{analyze_blame,stream_blame,_explain_blame,
  _stream_explain_blame}` 제거(헬퍼는 그래프 노드가 재사용). `service.py`는 헬퍼 + `/ask`·`is_noise_commit`·`extract_keywords` 진입점만 유지.
- [x] **`explain` 노드 `RetryPolicy`** — `RetryPolicy(max_attempts=3, retry_on=_is_retryable_bedrock)`로
  일시적 오류(Throttling/timeout/5xx)만 자동 재시도. 권한·검증 오류는 즉시 degraded. 재시도까지 소진된 하드 실패는
  호출 경계(`stream_blame_graph`/`run_blame_graph`)가 degraded 응답으로 마감(캐시 미저장). `test_explain_retries_transient_throttle_then_succeeds`로 검증.
- [ ] **(선택) `_suggest_improvement` 처리** — 여전히 미사용(사이드바 미렌더). "추후 UI 도입 시 재사용" 주석과 함께 잔존.

> ℹ️ **degraded 동작 차이**: explain이 예외를 그대로 올리도록 바꿔 RetryPolicy가 작동하므로, 하드 실패 시 degraded 응답은
> 메타(commit/author/date/ticket/team/changeStats/lineHistory)만 담고 `relatedChanges`는 비운다(과거 in-node 폴백은
> assemble까지 거쳐 relatedChanges를 포함했음). 오류 응답에서의 사소한 차이로, 정상 응답은 동일.

---

### 🧹 documents 잔재 정리 (GitHub Issue 전환 후속)

- [ ] **`documents` / `document_links` 물리 테이블 DROP 마이그레이션 생성**
  - ORM은 이미 제거됨. Alembic revision으로 `DROP TABLE documents, document_links` 추가.
- [ ] **고아 코드 제거 결정**
  - `features/documents/`(router/service/schemas) — `main.py`에 미등록이라 라우팅 안 됨.
  - `core/knowledge_base.py`, `core/doc_index.py` — 온보딩 semantic 경로에서만 참조. 온보딩 방향 확정 후 정리.
  - `config.py`의 `DOCUMENTS_DIR`/`DOC_INDEX_*`/`TRACE_BACKFILL_MIN_CONFIDENCE` 등 — 위 결정에 따라 제거.

---

### 🔵 컨텍스트 블레임 개선 (신예진)

#### ✅ 완료
- [x] LLM에 실제 diff hunk 포함 (`git show -p`)
- [x] async 이벤트 루프 블로킹 해소 (`asyncio.to_thread`)
- [x] 프론트 캐시 stale 무효화 (`onDidChangeTextDocument`)
- [x] **SSE 스트리밍 전환** — meta/delta/done 3프레임, 듀얼 모드(JSON↔SSE)
- [x] 노이즈 커밋 LLM 우회 (`commit_classifier` 공유, `_noise_response`)
- [x] GitHub/GitLab PR·Issue 조회 메모이즈 (`vcs.py` `lru_cache(128)`)
- [x] GitLab(MR→Issue) 지원 + PR 본문 없는 커밋 폴백(`find_issues_from_commit_message`)
- [x] 첨부 URL 도메인 화이트리스트(`CODEWHY_ATTACHMENT_DOMAINS`)
- [x] diff 토큰 관리 — `_truncate_diff` hunk 헤더 우선 보존
- [x] 관련 변경/PR 범위 — 페이지네이션 + "외 N개" 카드
- [x] ask_followup 맥락 재사용 (`_CONTEXT_CACHE`)
- [x] 블레임 단위 테스트 (`backend/tests/blame/`)

#### 🟡 미완
- [ ] **'라인 수정 이력' 이슈 배지 → 실제 이슈 이동 링크 연결 (이슈 기능 개발 후)**
  - 현상: `sidebar.ts::renderHistory`의 '이슈 N' 배지는 `data-action="openIssueTodo"`로 임시 안내만 뜸
    (`view.ts::onOpenIssueTodo`). 실제 URL이 없어 이동 불가.
  - 진행 시: 백엔드 `service.py::_build_line_history`/`schemas.py::LineHistoryEntry`에 이슈 URL 필드 추가
    (`vcs.find_issues_from_commit_message` 재사용) → 프론트 배지 `openIssueTodo` → 기존 `openIssue`(+url)로 전환,
    `onOpenIssueTodo` 핸들러 제거.
- [ ] **노이즈 응답 문구 확정 (UX)** — `service.py::_build_noise_explanation`의 임시 폴백
  (`[자동 분류] {label} 정비 커밋입니다 — "{quote}"`)을 사이드바 톤에 맞춰 확정.
- [ ] ask_followup Q&A DB 누적 (보류 — UX 결정 대기)
- [ ] **crud DB 통합 테스트 (Postgres testcontainer)** — `save_blame` ON CONFLICT / `get_cached_blame` dedup / JSONB round-trip 검증.
- [ ] `uncommitted_response`의 `_UNCOMMITTED_MESSAGE`/`_count_linked_issues` 등 사용자 작성 구역 마무리 점검

---

### 🟣 타임라인 (박성태)

#### ✅ 완료 (구 `TIMELINE_OPTIMIZATION_PLAN.md` / `TIMELINE_FOLLOWUP.md` 항목 포함 — 두 문서는 폐기 예정)
- [x] 마일스톤 시각화(통합 패널 '타임라인' 탭)
- [x] SSE 스트리밍 전환 (`stream_file_summary`, `stream_summary`)
- [x] 노이즈 면제 캐시 키 (`compute_commit_set_hash` + `filter_meaningful`)
- [x] JSON 파싱 실패 폴백 (`parse_ai_response` raw 폴백)
- [x] **일괄 분석 폐지(lazy on-demand)** — `/files/analyze`·`timeline/tasks.py`·`project/tasks.py`·`ai/graph.py`·`ai/project_graph.py` 제거, `project/initialize`는 ACK만
- [x] **공통 분류기 추출** — `core/commit_classifier.py` 신설, `ai/graph.py`의 `_SKIP_TYPES`/`_classify_commit` 중복 제거(SSOT 확립)
- [x] **캐시 적중 hot path bulk upsert** — `upsert_commits_bulk`/`link_commits_files_bulk`로 N+1 → 2 statement
- [x] `get_cached_summary` 디버그 쿼리 DEBUG 레벨 가드 + `project/timeline`의 `folder_name` NameError 수정

#### 🟡 미완 (plan 문서에서 끝까지 안 끝난 항목 — 인라인 이관)
- [ ] **`_get_file_diff` 정보 범위 정책 결정** — 캐시 키는 파일의 *전체 커밋 셋 해시*인데, LLM에 실제로 들어가는 diff는
  `git show HEAD -- file`의 **최신 1개**뿐(`timeline/service.py`). `commits_text`는 폴백이라 무게중심이 HEAD에 쏠린다.
  → 현 상태 유지 vs 증분 요약(누적 summary + 신규 커밋)으로 갈지 product 결정 필요.
- [ ] **(선택) 캐시 적중 시 upsert 자체 우회** — bulk upsert는 적용됨. 요청 커밋 해시 셋이 DB 최신 N건과 같으면
  upsert를 통째로 스킵해 hot path를 read-only로 만드는 추가 최적화는 미적용.
- [ ] **(소소) `timeline/service.py` 커밋 파싱 SSOT** — 프롬프트 관점(type/domain) 추출용 `_COMMIT_RE`/`_parse_commit`이
  `core/commit_classifier.classify_commit`와 정규식이 중복. 분류기 재사용 검토.
- [ ] (선택) 첫 진입 UX — 스트리밍으로 무응답 구간은 완화됐으나, 확장 측 동시 호출 dedup(같은 파일 in-flight 가드)은 미정.

---

### 🟢 요구사항 역추적 (전준민)

- [x] GitHub Issue 기반 전환 (issue/ticket/semantic)
- [x] matchType별 신뢰도 표시 (통합 패널 '이슈' 탭)
- [ ] Issue 첨부(PDF 등) 사이드바 내 미리보기
- [ ] semantic 매칭 결과 캐시 (반복 조회 비용 절감)

---

### 🔧 인프라·보안·배포

- [ ] CORS `allow_origins=["*"]` → 배포 시 확장 origin으로 제한
- [ ] 에러 응답 표준화 (현재 기능별 `HTTPException(500, f"...: {e}")` 패턴)
- [ ] `backend/Dockerfile` 배포 파이프라인(CI) + 마이그레이션 자동 실행
- [ ] Bedrock 호출 비용/레이트리밋 모니터링
- [ ] 타임라인/온보딩 단위 테스트 추가 (블레임은 `backend/tests/blame/`에 존재)

---

### 💡 기능 확장 아이디어

- [ ] 블레임/타임라인/역추적 간 상호 내비게이션 (같은 커밋·티켓으로 연결) — 이미 통합 패널 한 곳에 있어 연결 용이
- [ ] 다국어/모노레포·서브모듈 레포 경로 처리

---

## 11. 커밋 메시지 규칙

```
<Type>[Domain]: <Description>

- 상세 내역 1 (선택)
- 상세 내역 2 (선택)
```

### Type 정의

AI 타임라인 요약·블레임 노이즈 우회가 커밋의 성격을 파악하는 기준입니다.
(`core/commit_classifier.py`가 `^(\w+)(?:\[(domain)\])?:\s*(.+)$` 정규식으로 파싱.)

| Type | 설명 | AI 인식 |
| ---- | ---- | ------- |
| `feat` | 새로운 기능 추가 | "기능 추가의 역사" |
| `fix` | 버그 수정 | "디버깅 및 안정화의 역사" |
| `refactor` | 기능 변화 없는 코드 구조 개선 | "구조 개선의 역사" |
| `perf` | 성능 개선 | "성능 최적화의 역사" |
| `docs` | 문서 수정 (README 등) | **노이즈 — 분석/캐시 제외** |
| `test` | 테스트 추가 / 수정 | **노이즈 — 분석/캐시 제외** |
| `chore` | 자잘한 설정 변경 (패키지 설치 등) | **노이즈 — 분석/캐시 제외** |

> `SKIP_TYPES = {test, chore, docs}` — 블레임은 정형 응답으로 우회, 타임라인은 캐시 키 계산에서 제외.

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
  (UI는 가능하면 `contextBlame`의 통합 패널 탭으로 통합 — 별도 Webview 신설 지양.)
- **백엔드에 기능 추가**: `features/<기능>/{router,service,schemas}.py` 구성 → `main.py`에 `include_router`.
- **공유 데이터**(commit/file/repo)는 `db/crud_common.py`의 upsert 헬퍼 재사용.
- **커밋 분류**(노이즈 판정)는 `core/commit_classifier.py` 재사용 — 새 정규식 만들지 말 것.
- **LLM 호출**: 스트리밍이면 `core/bedrock.py::get_bedrock_llm`(`astream`), 비스트리밍 + 프롬프트 캐싱이면
  `core/ai_client.py::call_bedrock`.
- **스키마 변경**은 ORM 수정 후 Alembic autogenerate.
- **외부 연동 추가** 시 미설정 환경에서 no-op/폴백하도록 작성(로컬 개발 보호).

### 공용 코드 수정 규칙

다음 영역은 세 명이 함께 쓰므로 **PR/팀 합의 후** 수정합니다.

- `src/extension.ts`, `src/shared/**`, `src/features/contextBlame/{view,sidebar}.ts`(통합 패널 호스트)
- `backend/app/main.py`, `backend/app/core/**`, `backend/app/db/**`, `backend/app/ai/**`
- `package.json`의 `contributes.commands`/`menus`/`configuration`

새 명령을 추가하거나 응답 스키마를 바꿀 때 외에는 이 영역을 건드릴 일이 거의 없습니다.

---

질문이나 응답 스키마 변경이 필요하면 팀 채널에서 공유해주세요.
