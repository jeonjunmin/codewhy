# CodeWhy 개발 가이드

> 현재 소스 코드(`master` 기준) 기반으로 정리한 **단일 개발 레퍼런스**입니다.
> 개발 현황·구조·담당 분장·커밋 규칙·TODO를 한 곳에서 관리합니다.
> 마지막 정리: 2026-06-07 (블레임↔타임라인 공통 처리 원칙 §6에 신설, §10에 보정 항목 추가, §6 타임라인 구현 흐름 상세 추가 — to-be 기준)

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

요구사항 문서를 코드와 잇는 방식은 **기능별로 다릅니다.** 장기적으로 두 갈래를 통합할지는 §10 블로커 항목 참고.

| 기능 | 현재 동작 |
|---|---|
| 컨텍스트 블레임 | **GitHub Issue 본문 + 첨부 파일을 실시간 조회**. 별도 업로드 불필요. `commit → PR → Closes #N → Issue → attachments` 체인. |
| 요구사항 역추적 | **Document 저장소(`documents`/`document_links` 테이블) 기반**. 온보딩 시 사전 매핑 + 커밋 키워드 기반 Bedrock KB 검색. GitHub Issue 첨부로의 전환은 §10에 계획되어 있으며 아직 실행되지 않음. |
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
                                               │  - PR 조회         │   (블레임 전용:
                                               │  - Issue 본문/첨부  │    Issue 본문·첨부를
                                               └──────────────────┘    LLM 맥락으로 전달)
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
3. 노이즈 커밋(test/chore/docs)은 LLM 호출을 건너뛰고 정형 응답으로 대체 — **예정** (§10 P1, 타임라인과 처리 원칙 정렬)

### ✅ 타임라인 요약 (Timeline Summary) — 동작 가능

`POST /api/timeline/summary`

- LangGraph **Map-Reduce 파이프라인** 완성(`features/timeline/graph.py`)
- 노이즈 커밋(test/chore/docs) 제거, 청크 20개 단위, JSON 파싱 실패 시 최대 2회 재시도, 실패 시 폴백 응답
- **캐시**: `timeline_summaries` UNIQUE(file_id, commit_set_hash) — `compute_commit_set_hash`는 정렬된 커밋 해시 SHA-256으로 구현됨
- 프론트엔드 Webview에서 마일스톤 타임라인 시각화(세로선·날짜 칩·설명 카드) 제공
- map 단계는 현재 **순차 실행** — Bedrock 호출 병렬화(LangGraph `Send()` API)는 §10 선택 항목
- **비용 정책**: 일괄 prefetch 폐지(lazy on-demand) + 캐시 키에서 노이즈 커밋 제외 — 별도 plan 참조 (`TIMELINE_OPTIMIZATION_PLAN.md`)

#### 구현 흐름 상세

> ⚠️ **본 다이어그램은 `TIMELINE_OPTIMIZATION_PLAN.md` 적용 후의 to-be 흐름이다.** 현재 코드는 (a) 일괄 분석(`/files/analyze`, `/project/initialize` 백그라운드 태스크)이 살아 있고, (b) 캐시 키 계산이 노이즈 커밋을 포함해 SHA-256으로 묶으며, (c) 분류기는 `features/timeline/graph.py` 내부에 갇혀 있다. plan 완료 시 아래 흐름으로 정렬된다.

> 핵심 설계 원칙: **요약 단위는 파일이고, 캐시 키는 그 파일의 "의미 있는 커밋 집합"이다.**
> 노이즈 커밋(test/chore/docs)은 캐시 키 계산과 LLM 호출 양쪽에서 모두 제외한다. 같은 분류 기준을 블레임이 import해 쓰므로, 정의는 `backend/app/core/commit_classifier.py` 단일 소스에 둔다(§10 인프라).

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
        keyhash = SHA-256(sorted(target.hash))
    │
    ▼
[5] 캐시 조회  →  timeline_summaries WHERE (file_id, commit_set_hash=keyhash)
    │
    ├─ 적중 ──▶  저장된 {summary, milestones} 즉시 반환  (Bedrock 0회)
    │
    └─ 미스 ──▶
            [6] LangGraph 실행 (features/timeline/graph.py — StateGraph)
                  ① classify_and_split — 청크 20커밋 단위 분할
                                          (분류는 [4]에서 끝났으므로 여기는 분할만)
                  ② map_summarize     — 청크별 ChatBedrock 호출 (순차, C회)
                                          C = ceil(의미있는 커밋 수 / 20)
                  ③ reduce_merge      — 중간 요약 통합 → 최종 JSON
                                          (ChatBedrock 1회)
                  ④ parse_output      — JSON 파싱
                  ⑤ 실패 시           → increment_retry → reduce_merge 재시도
                                          (MAX_RETRIES = 2)
                  ⑥ 끝까지 실패       → apply_fallback
                                          (LLM 원본 앞 300자 + 최근 5커밋)
            [7] timeline_summaries upsert (file_id, commit_set_hash, summary, milestones)
            [8] 응답 반환 → 프론트가 Webview에 마일스톤 카드로 렌더링
```

**Bedrock 호출 횟수 (캐시 미스 1회 분석 기준)**:
- 정상 경로: `C + 1` 회 (map C회 + reduce 1회)
- 재시도 발생 시: 최대 `C + 1 + 2` 회 (reduce만 재호출, 최대 2회)
- 폴백 진입 시: 위 호출 후 추가 호출 없음 — `apply_fallback`은 LLM 미호출

**캐시 무효화 정책**:
- 의미 있는 커밋(feat/fix/refactor/perf/…)이 추가/변경되면 `keyhash` 가 바뀌어 자동 재분석.
- 노이즈 커밋(test/chore/docs)만 푸시된 경우 `filter_meaningful`이 그것을 제외 → `keyhash` 불변 → **재분석 안 일어남**(블레임의 §6 공통 처리 원칙 #2 정렬).

**LLM 진입점**:
- `app.core.bedrock.get_bedrock_llm()` → `langchain_aws.ChatBedrock` 인스턴스를 LangGraph 노드에서 사용 (`HumanMessage` + `.invoke()`).
- 블레임이 쓰는 `app.core.ai_client.call_bedrock`(boto3 Converse 직접)과는 다른 진입점. 같은 Bedrock 모델을 호출하지만 SDK 레이어가 다르며, 프롬프트 캐싱(`cachePoint`)은 현재 타임라인 경로에서 미사용 — 청크가 매번 다른 본문이므로 캐싱 이득이 작기 때문(§6 공통 처리 원칙 표의 5번 행 참고).

**비용 최적화**:
1. **Lazy on-demand** — 사용자가 실제로 연 파일만 분석 (TIMELINE_OPTIMIZATION_PLAN.md A1)
2. **노이즈 면제 캐시 키** — 의미 없는 커밋이 캐시를 깨지 않음 (TIMELINE_OPTIMIZATION_PLAN.md C)
3. **청크 분할** — 큰 파일도 토큰 안전, map 단계 병렬화 여지(§10 선택)
4. **재시도 상한 + 폴백** — JSON 파싱 실패가 무한 호출로 번지지 않음

### ✅ 요구사항 역추적 (Requirement Trace) — 동작 가능 (현 구현은 Document 저장소 기반)

`POST /api/trace/requirement`

코드 라인에서 연관 기획 문서를 찾아 보여주는 기능. 현재 구현은 **`documents`/`document_links` 테이블에 미리 적재된 문서**를 매칭합니다. GitHub Issue 첨부 실시간 조회로의 전환은 §10에 계획되어 있으며 아직 실행 전입니다.

**추적 경로 (실제 코드)**:

```
코드 라인 → git blame → commit
    → ① 커밋/브랜치에서 티켓 번호 추출   → DocumentLink(link_type="ticket")
    → ② 온보딩이 사전 적재한 매핑 조회   → DocumentLink(link_type="commit"|"file")
    → ③ 커밋 메시지 키워드로 Bedrock KB 검색 → Passage → Document
    → matchType/confidence 와 함께 UI에 표시
```

| matchType | 방식 | 확신도 |
|-----------|------|--------|
| `ticket` | 커밋/브랜치 티켓 번호 → `DocumentLink(link_type="ticket")` | 확정 |
| `backfill` | 온보딩이 사전 생성한 commit/file 단위 매핑 | 높음 |
| `semantic` | 커밋 메시지 키워드 → Bedrock Knowledge Base 검색 | 추정 (낮음) |

> 위 어느 경로에도 **GitHub Issue API 호출은 포함되지 않습니다.** "Closes #N 파싱 → Issue 첨부 수집"은 현재 컨텍스트 블레임에만 구현되어 있습니다.

### 🧪 브라운필드 온보딩 (Onboarding) — 개발 중

`POST /api/onboarding/backfill`

- 레거시 레포 전체 git 히스토리를 훑어 커밋↔Issue 역링크를 사전 생성
- 부분 유니크 인덱스로 재실행 중복 방지(idempotent)

---

### 🔁 공통 처리 원칙 (블레임 ↔ 타임라인)

같은 프로젝트의 두 기능이 비용·속도 정책에서 어긋나면 운영 일관성이 깨지고 회귀가 누적된다. 두 기능 모두 아래 다섯 원칙을 따른다. 새 기능을 추가할 때도 이 원칙에 정렬해 설계한다.

| # | 원칙 | 블레임 | 타임라인 |
|---|------|-------|---------|
| 1 | **Lazy on-demand** — 사용자 액션 시점에만 분석, 일괄 prefetch 금지 | ✅ 적용 (라인 클릭 1회) | ✅ A1 전환 후 적용 (`TIMELINE_OPTIMIZATION_PLAN.md`) |
| 2 | **노이즈 커밋 LLM 우회** — test/chore/docs는 LLM 호출·캐시 무효화에서 모두 제외 | ✅ 적용 (`analyze_blame` 진입 분기, 2026-06-07) | ⏳ 캐시 키 면제 예정 (별도 plan) |
| 3 | **공유 백본** — repo/commit/file upsert는 항상 `db/crud_common.py` 경유 | ✅ | ✅ |
| 4 | **외부 API 메모이즈** — GitHub PR·Issue 조회는 요청 스코프 캐시로 중복 호출 차단 | ✅ 적용 (`vcs.py` lru_cache 128, 2026-06-07) | — (외부 API 미사용) |
| 5 | **폴백 정책 일관** — 외부 의존성 미설정·실패 시 예외 대신 동등 형식의 폴백 응답 | ✅ `[Bedrock 미연동] …` | ✅ `apply_fallback` + `_fallback_summary` |

원칙 2, 4를 위해 **공통 커밋 분류기**(`backend/app/core/commit_classifier.py`)를 신설할 예정 — `_classify_commit`/`SKIP_TYPES`를 타임라인 graph.py에서 추출해 블레임도 동일한 기준으로 호출 우회를 적용한다. (§10 인프라 참고)

#### LLM 호출 방식 비교 (Bedrock + LangChain)

같은 AWS Bedrock 모델을 부르지만 진입점·구조·캐싱 활용이 다르다. 이 차이는 도메인 요구에서 비롯한 것이며, 통일이 아니라 **이해**가 목적이다.

##### 한 눈에 — "직통 전화" vs "콜센터 시스템"

```
블레임                              타임라인
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  service.py                          graph.py (LangGraph 노드)
      │                                     │
      │  boto3로 직접 호출                    │  LangChain에 위임
      ▼                                     ▼
  [AWS Bedrock]                   [LangChain ChatBedrock]
                                          │  (내부에서 boto3 사용)
                                          ▼
                                    [AWS Bedrock]

  "직통 전화"                        "콜센터 통해서 연결"
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

##### 타임라인 — LangChain + LangGraph (`core/bedrock.py` + `features/timeline/graph.py`)

```python
# core/bedrock.py
def get_bedrock_llm():
    return ChatBedrock(...)        # LangChain 객체 반환

# graph.py — LangGraph 노드 안에서
llm = get_bedrock_llm()
llm.invoke([HumanMessage(content=prompt)])   # LangChain 방식
```

LangGraph의 `StateGraph` 노드가 LangChain 객체를 기대하므로, 5개 노드가 연결된 파이프라인 전체를 간결하게 구성할 수 있다.

```
classify_and_split
      │
      ▼
map_summarize ─── ChatBedrock.invoke() × 청크 수 (C회)
      │
      ▼
reduce_merge ──── ChatBedrock.invoke() × 1회
      │
      ▼
parse_output ── 실패? → increment_retry → reduce_merge (재시도, MAX=2)
                성공? → END
```

##### 비교 요약

| 측면 | 블레임 | 타임라인 |
|---|---|---|
| 진입점 | `core/ai_client.py::call_bedrock` | `core/bedrock.py::get_bedrock_llm` |
| SDK 레이어 | boto3 **Converse API 직접** 호출 | **LangChain `ChatBedrock`** 인스턴스 |
| 오케스트레이션 | 단순 순차 (설명 1회 → 제안 1회) | **LangGraph `StateGraph`** (5 노드: split → map → reduce → parse → retry/fallback) |
| 메시지 구성 | `[context, cachePoint, prompt]` 3파트 | `HumanMessage(prompt)` 단일 |
| **프롬프트 캐싱** | ✅ 활용 — 같은 context 블록 두 번 보내 캐시 적중 | ❌ 미활용 — 청크마다 본문이 달라 효과 작음 |
| 호출 횟수/요청 | 2회 고정 (설명 + 제안) | 가변: `청크 수 + 1` (+ 재시도 최대 2) |
| 토큰 가드 | `_MAX_DIFF_CHARS=2000` head-only 잘라내기 | 청크 20커밋 단위 분할 |
| 폴백 | `[Bedrock 미연동] …` 메시지 | `apply_fallback` (LLM 원본 앞 300자 + 최근 5커밋) |

**왜 두 진입점인가**: 블레임은 "프롬프트 캐싱으로 비용 깎기"가 우선이라 Converse를 직접 부르는 게 유리하고, 타임라인은 "여러 노드로 map-reduce·재시도·폴백을 오케스트레이션"하는 게 우선이라 LangGraph의 상태 머신이 적합하다. 두 진입점을 한 SDK로 합치려면 한쪽 도메인 요구를 양보해야 하므로 현재는 **공존을 인정**한다. 단, `core/commit_classifier.py`처럼 **공통 데이터/규칙은 공유 모듈**로 끌어내는 정책은 유지한다.

---

## 7. 데이터 모델 (통합 스키마 6테이블 — 목표; 현재는 documents/document_links 포함 8테이블)

```
repositories ─┬─ commits ─┬─ commit_files ─ files
              │           │
  blame_explanations ─────┘         timeline_summaries

[과도기] documents ── document_links   (역추적 전용, §10에서 제거 예정)
```

| 테이블 | 역할 | 핵심 제약 |
| --- | --- | --- |
| `repositories` | 레포 식별자 루트 | identifier UNIQUE |
| `commits` | git 커밋 (블레임·타임라인 공유) | UNIQUE(repo_id, commit_hash) |
| `files` | 레포 내 파일 경로 | UNIQUE(repo_id, file_path) |
| `commit_files` | 커밋↔파일 N:M + 변경량 | (commit_id, file_id) PK |
| `blame_explanations` | 블레임 AI 결과 캐시 | UNIQUE(file_id, commit_id) |
| `timeline_summaries` | 타임라인 요약 캐시 | UNIQUE(file_id, commit_set_hash) |

> **`documents` / `document_links` 테이블 — 결정됨, 미실행 (2026-06-06)**
> 요구사항 문서를 별도 업로드·저장하던 방식에서, **GitHub Issue 첨부 파일을 실시간 조회**하는 방식으로 전환하기로 결정했습니다.
> - 문서 메타데이터를 DB에 저장할 필요 없음 — GitHub API가 원천
> - 커밋↔문서 매핑도 DB에 저장할 필요 없음 — `commit → PR → Issue → attachments` 체인으로 파생
> - 분석 결과(출처 URL 포함)는 `blame_explanations`의 JSON 컬럼에 이미 캐시됨
>
> **현재 상태(실제 코드 기준)**: ORM(`db/models.py`의 `Document`/`DocumentLink`), 라우터(`features/documents/`, `main.py`의 include_router), 백필(`features/onboarding/backfill.py::_link_passages`)이 모두 그대로 살아 있으며 DROP 마이그레이션도 없습니다. **컨텍스트 블레임만 새 방식(GitHub Issue 실시간 조회)을 사용**하고, **역추적은 여전히 이 테이블에 의존**합니다.
>
> 정리 작업은 §10 인프라 항목 참고.

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
| POST | `/api/trace/requirement` | 전준민 | `{filePath, line, repoPath}` | `{documents:[{documentId, name, page?, excerpt?, downloadUrl, matchType?, confidence?}]}` |
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
| `CODEWHY_ATTACHMENT_DOMAINS` | 첨부로 인정할 외부 도메인 화이트리스트 (쉼표 구분, 예: `notion.so,confluence.atlassian.com`) | 확장자/사용자업로드 휴리스틱만 사용 |

> **설계 미덕**: 거의 모든 외부 연동이 미설정 시 *no-op/폴백*으로 동작 → 로컬에서 일부 기능만으로도 깨지지 않음.

### DB 마이그레이션

```bash
cd backend && alembic upgrade head        # 스키마 적용
alembic revision --autogenerate -m "..."  # 스키마 변경 시
```

---

## 10. TODO 리스트

### 🔴 블로커 — 결정/정리 필요

- [ ] **역추적 아키텍처 갈래 결정 (문서 ↔ 코드 불일치)**
  - 현상: §1·§6·§7에 적힌 "GitHub Issue 첨부 실시간 조회" 방향과, 실제 코드(`features/traceability/`가 `documents`/`document_links` 테이블에 의존)가 정반대.
  - 선택지 A: 역추적을 블레임처럼 GitHub Issue 첨부 조회로 전환 → `_by_issue` matchType 신설, `documents`/`document_links`/`features/documents/`/`backfill._link_passages` 삭제, 응답 스키마(`name`/`downloadUrl` → `title`/`url`)도 동시 조정.
  - 선택지 B: 현 Document 저장소 기반을 정식 방안으로 유지 → §1·§6·§7 본문을 그에 맞춰 보정하고, "GitHub Issue 전환" 결정을 철회.
  - 어느 쪽을 택하든 한 번에 결정해야 §1, §6 역추적, §7, §10이 다시 일관됨.

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

- [ ] 역추적 시맨틱 폴백 결과 캐시 (blame처럼 file_id/commit_id 키)
- [ ] 타임라인 map 단계 Bedrock 호출 병렬화 (LangGraph `Send()` API)
- [x] **diff 토큰 관리 전략 통일 (블레임 측)** (2026-06-07)
  - `_truncate_diff`가 hunk 헤더(`@@ ... @@`) 우선 보존 — 통째로 살릴 수 있는 hunk 는 살리고, 잘린 hunk 들의 헤더만 `[잘린 hunks — 헤더만 보존]` 블록으로 끝에 모아 LLM 이 어느 영역이 잘렸는지 인지하게 함. patch 가 아니면 head+tail 폴백.
  - 타임라인 청크 전략 정렬은 별도 plan(타임라인 map 청크 사이즈 정책) 후속.

---

### 🔧 인프라·보안·배포

- [ ] **공통 커밋 분류기 추출 (블레임/타임라인 공유)**
  - 신규 파일: `backend/app/core/commit_classifier.py`
  - 내용: `_classify_commit`(정규식 `^(\w+)(?:\[([^\]]+)\])?:\s*(.+)$`), `SKIP_TYPES = {"test", "chore", "docs"}`
  - 현재 위치: `backend/app/features/timeline/graph.py` 내부 — 블레임이 import 불가
  - 사용처: 타임라인 `compute_commit_set_hash`(별도 plan), 블레임 `analyze_blame` 노이즈 우회(§10 P1)
  - 효과: §6 공통 처리 원칙 #2 정렬

- [ ] **`documents` / `document_links` 테이블 및 관련 코드 삭제 (결정만 됨, 미실행)**
  - 전제: §10 블로커의 "역추적 아키텍처 갈래 결정"에서 선택지 A 채택 시에만 진행. 선택지 B면 이 항목은 폐기.
  - ORM: `db/models.py`에서 `Document`/`DocumentLink` 모델 제거 — 현재 [models.py:178-229]에 잔존
  - 라우터: `features/documents/` 폴더 전체 삭제, `main.py:55-59`의 `include_router(documents_router, ...)` 제거
  - 백필: `features/onboarding/backfill.py::_link_passages`(99-135행)와 그 호출부 정리
  - 역추적 서비스: `features/traceability/service.py`의 `_by_ticket`/`_by_backfill`(Document/DocumentLink 의존) 재설계
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
- [x] 마일스톤 타임라인 시각화 (Webview) — `src/features/timelineSummary/view.ts`에 세로선/날짜 칩/설명 카드 구현됨
- [x] `JSON 파싱 실패 시 폴백 처리` — `features/timeline/graph.py::apply_fallback`(LLM 원본 앞 300자 + 최근 커밋 5개), `_fallback_summary`(Bedrock 미연동) 2단 폴백 구현됨
- [ ] map 단계 Bedrock 호출 병렬화 (LangGraph `Send()` API) — 현재 순차 실행, `graph.py` 주석에도 명시

#### 전준민 (Requirement Trace)
- [ ] **선결**: §10 블로커의 "역추적 아키텍처 갈래 결정" — 선택지 A(GitHub Issue 전환) 시 아래 두 항목은 그 위에서 진행해야 의미가 있음
- [ ] GitHub Issue 첨부 파일 목록을 UI에 보여주는 Webview 구현 (선택지 A 채택 시)
- [~] matchType별 신뢰도 표시 UI — `src/features/requirementTrace/view.ts`의 QuickPick에 배지+% 부분 구현. Webview 상세 뷰로의 확장만 남음

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
