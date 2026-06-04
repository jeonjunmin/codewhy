# CodeWhy 개발 가이드

> 현재 소스 코드(`master` 기준) 기반으로 정리한 개발 현황·구조·할 일 문서입니다.
> 마지막 정리: 2026-06-03

---

## 1. 프로젝트 개요

CodeWhy는 **"코드의 왜(why)를 설명하는"** VSCode 확장 + Python 백엔드입니다.
`git blame`이 *누가/언제*만 알려주는 한계를, AI(RAG)로 *왜 바꿨는지·기획 의도*까지 메워 줍니다.

세 가지 핵심 기능을 제공합니다.

| 기능 | 한 줄 설명 | 담당 |
| --- | --- | --- |
| 컨텍스트 블레임 (Context Blame) | 선택한 라인의 변경 *이유*를 기획서 근거와 함께 설명 | 개발자 A (신예진) |
| 타임라인 요약 (Timeline Summary) | 파일의 전체 커밋 흐름을 한 문단 + 마일스톤으로 요약 | 개발자 B (박성태) |
| 요구사항 역추적 (Requirement Trace) | 코드 → 연관 기획 문서를 다단계로 찾아 다운로드 링크 제공 | 개발자 C (전준민) |

추가로 **브라운필드 온보딩**(레거시 레포 일괄 적재) 기능이 개발 중입니다.

---

## 2. 아키텍처

```
┌──────────────────────┐      HTTP(axios)      ┌──────────────────────────┐
│  VSCode 확장 (TS)     │ ───────────────────▶ │  FastAPI 백엔드 (Python)   │
│  src/                │                       │  backend/app/             │
│  - 우클릭 명령 3종     │ ◀─────────────────── │  - /api/blame             │
│  - Webview 사이드바    │      JSON 응답         │  - /api/timeline          │
└──────────────────────┘                       │  - /api/trace             │
                                               │  - /api/documents         │
                                               │  - /api/onboarding        │
                                               └───────────┬──────────────┘
                                                           │
                          ┌────────────────────────────────┼─────────────────────────┐
                          ▼                                ▼                           ▼
                  ┌───────────────┐              ┌──────────────────┐        ┌──────────────────┐
                  │  Git CLI       │              │ PostgreSQL (RDS) │        │  AWS Bedrock      │
                  │  (subprocess)  │              │  통합 스키마 8테이블 │        │  - Converse(LLM)  │
                  │  blame/log/diff│              │  + 캐시           │        │  - Knowledge Base │
                  └───────────────┘              └──────────────────┘        │  - LangGraph 요약  │
                                                                             └──────────────────┘
```

**데이터 흐름의 공통 패턴**: 확장이 로컬 git 정보(repoPath/filePath/line, 또는 커밋 목록)를 백엔드로 보내면,
백엔드가 ① git으로 사실 추출 → ② PostgreSQL 공유 백본에 upsert + 캐시 조회 → ③ 캐시 미스 시 Bedrock 호출 → ④ 결과 캐시 후 반환.

---

## 3. 기술 스택

| 영역 | 기술 |
| --- | --- |
| VSCode 확장 | TypeScript 5.9, VSCode Extension API (`^1.118.0`), axios |
| 백엔드 | Python 3.11+, FastAPI, Uvicorn, Pydantic v2 / pydantic-settings |
| DB | PostgreSQL(RDS), SQLAlchemy 2.0(async/asyncpg), Alembic(psycopg2) |
| AI | AWS Bedrock — Converse API(LLM), Knowledge Base(RAG retrieve), LangGraph(타임라인 Map-Reduce) |
| 문서 파싱 | pypdf (PDF 페이지 수) |
| Git | Git CLI subprocess (`app/core/git.py`) |

> ⚠️ README에는 캐시로 "AWS DynamoDB"라고 적혀 있으나, **실제 코드는 PostgreSQL(RDS)로 전환 완료**되었습니다(커밋 `0493e72`). README 갱신 필요.

---

## 4. 디렉터리 구조

```
codewhy/
├── src/                              # VSCode 확장 (TypeScript)
│   ├── extension.ts                  # 진입점 — 기능 register 호출만
│   ├── shared/                       # http 클라이언트, editor 유틸, 공용 타입
│   └── features/
│       ├── contextBlame/             # command / view(webview) / api / sidebar
│       ├── timelineSummary/          # command / view / api
│       └── requirementTrace/         # command / view / api
│
└── backend/app/                      # FastAPI 백엔드
    ├── main.py                       # 앱 생성 + 라우터 5개 등록 + DB 연결 확인
    ├── core/                         # 공용 모듈
    │   ├── git.py                    # blame/log/diff/branch 추출
    │   ├── ai_client.py / bedrock.py # Bedrock Converse / LangChain ChatBedrock
    │   ├── knowledge_base.py         # KB retrieve (RAG)
    │   ├── doc_index.py   ⬅︎미커밋    # 문서 → S3(KB 데이터소스) 인덱싱
    │   ├── config.py                 # pydantic-settings 환경설정
    │   ├── tickets.py                # 커밋/파일명에서 티켓(PAY-2041) 추출
    │   └── vcs.py                    # GitHub/GitLab PR·MR 조회
    ├── db/
    │   ├── models.py                 # 통합 스키마 ORM (8테이블)
    │   ├── postgres.py               # async engine / get_db / Base
    │   └── crud_common.py            # repo/commit/file 공유 백본 upsert
    ├── ai/graph.py                   # LangGraph 타임라인 Map-Reduce 파이프라인
    ├── alembic/versions/             # 0001_init_schema, 0002_backfill_trace
    └── features/
        ├── blame/                    # router/service/crud/schemas
        ├── timeline/                 # router/service/crud/graph/schemas
        ├── traceability/             # router/service/schemas (다단계 추적)
        ├── documents/                # router/service/schemas (업로드/다운로드)
        └── onboarding/   ⬅︎미커밋     # router/backfill/schemas (브라운필드 백필)
```

---

## 5. 개발 완료 기능 현황

### ✅ 컨텍스트 블레임 (Context Blame) — 동작 가능
`POST /api/blame/context`, `POST /api/blame/ask`

- git으로 라인의 blamed 커밋(diff·메시지·변경 라인 수) 추출
- 커밋 메시지에서 키워드 추출(`extract_keywords`) → Bedrock KB에서 연관 기획서 단락 retrieve
- 코드 + 커밋 + 기획서 단락을 Bedrock Converse로 종합 → **변경 사유 한국어 설명**
- 부가: 티켓/팀 매핑, PR 단위 변경(`vcs.py`), 같은 티켓 후속 커밋 → "함께 일어난 일" 조립
- "AI에게 더 묻기"(`/ask`) 후속 질문 지원
- **캐시**: `blame_explanations` UNIQUE(file_id, line_no, commit_id) — 라인이 밀려 커밋이 바뀌면 자동 캐시 미스
- Bedrock 미설정 시 커밋 메시지로 폴백 → 로컬에서도 깨지지 않음

### ⚠️ 타임라인 요약 (Timeline Summary) — 캐시 키 미구현(블로커)
`POST /api/timeline/...`

- LangGraph **Map-Reduce 파이프라인** 완성(`ai/graph.py`): classify/split → map 요약 → reduce JSON → parse → 재시도/폴백
- 노이즈 커밋(test/chore/docs) 제거, 청크 20개 단위, JSON 파싱 실패 시 최대 2회 재시도 후 폴백
- **캐시**: `timeline_summaries` UNIQUE(file_id, commit_set_hash)
- 🚫 **`compute_commit_set_hash()`가 `NotImplementedError`** — 캐시 키 해시 함수가 비어 있어 현재 타임라인 호출은 실패함 (→ §7 TODO)

### ✅ 요구사항 역추적 (Requirement Trace) — 다단계 추적 동작
`POST /api/trace/requirement`

3단계 폴백으로 코드 라인 → 기획 문서를 찾음:
1. **ticket**: 커밋 티켓 == `document_links.ticket` 정확 매칭 (확정, confidence=None)
2. **backfill**: blamed 커밋에 사전 생성된 `document_links`(commit/file) — 백필 점수
3. **semantic**: 커밋 메시지로 즉석 KB 조회 (추정, 낮은 확신)

각 결과에 `matchType`/`confidence`를 실어 UI가 신뢰도를 구분 표시.

### ✅ 문서 업로드/다운로드 (Documents) — 동작 가능
`POST /api/documents`, `POST /api/documents/bulk`, `GET /api/documents/{id}/download`

- 기획 문서를 서버 디렉터리(`DOCUMENTS_DIR`)에 UUID 파일명으로 저장, DB엔 메타데이터만
- 업로드 시 수동 티켓 + 파일명 자동 추출 티켓 → `document_links(ticket)` 생성
- PDF면 페이지 수 추출(pypdf)
- bulk: 일괄 저장 + 시맨틱 인덱싱 + KB ingestion job 1회 트리거

### 🧪 브라운필드 온보딩 (Onboarding) — 개발 중 / 미커밋
`POST /api/onboarding/backfill` + `core/doc_index.py`

- 레거시 레포 전체 git 히스토리를 훑어 커밋↔문서 역링크를 **사전 생성**(조회 시점 비용 절감)
- 문서를 S3(Bedrock KB 데이터소스)에 올리고 `<key>.metadata.json` 사이드카에 `documentId` 심음
- 부분 유니크 인덱스로 재실행 중복 방지(idempotent)
- ⬅︎ `backend/app/features/onboarding/`, `backend/app/core/doc_index.py`, `alembic/versions/0002_backfill_trace.py`는 **아직 git 미추적(untracked)** 상태

---

## 6. 데이터 모델 (통합 스키마 8테이블)

```
repositories ─┬─ commits ─┬─ commit_files ─ files
              │           │
  blame_explanations ─────┘         timeline_summaries
  documents ─ document_links ── (ticket | commit_id | file_id)
```

| 테이블 | 역할 | 핵심 제약 |
| --- | --- | --- |
| `repositories` | 레포 식별자 루트 | identifier UNIQUE |
| `commits` | git 커밋(블레임·타임라인 공유) | UNIQUE(repo_id, commit_hash), ticket/author_email 인덱스 |
| `files` | 레포 내 파일 경로 | UNIQUE(repo_id, file_path) |
| `commit_files` | 커밋↔파일 N:M + 변경량 | (commit_id, file_id) PK |
| `blame_explanations` | 블레임 AI 결과 캐시 | UNIQUE(file_id, line_no, commit_id) |
| `timeline_summaries` | 타임라인 요약 캐시 | UNIQUE(file_id, commit_set_hash) |
| `documents` | 업로드 문서 메타데이터 | storage_key, indexed_at |
| `document_links` | 문서↔git 연결(ticket/commit/file) | 부분 UNIQUE(document_id, commit_id, link_type) |

> **설계 원칙**: "세 기능 모두에서 보여줄" 데이터(작성자·날짜·메시지·티켓)는 `commits`/`files`에 한 번만 저장하고, 기능별 산출물은 FK로 참조. 스키마 변경은 **반드시 Alembic autogenerate 마이그레이션**으로.

---

## 7. API 엔드포인트 요약

| Method | Path | 설명 |
| --- | --- | --- |
| GET | `/health` | 헬스체크 |
| POST | `/api/blame/context` | 라인 변경 사유 분석 |
| POST | `/api/blame/ask` | 블레임 맥락 위 후속 질문 |
| POST | `/api/timeline/...` | 파일 커밋 흐름 요약 (캐시 키 미구현 주의) |
| POST | `/api/trace/requirement` | 코드 → 기획 문서 다단계 역추적 |
| POST | `/api/documents` | 단일 문서 업로드(+티켓 연결) |
| POST | `/api/documents/bulk` | 문서 일괄 적재 + 시맨틱 인덱싱 |
| GET | `/api/documents/{id}/download` | 원본 문서 스트리밍 다운로드 |
| POST | `/api/onboarding/backfill` | 레포 전체 커밋↔문서 백필 (미커밋) |

---

## 8. 로컬 개발 환경

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

### 주요 환경변수 (`backend/.env`)
| 키 | 용도 | 미설정 시 동작 |
| --- | --- | --- |
| `DATABASE_URL` | PostgreSQL 접속 (런타임 asyncpg / Alembic psycopg2 자동 변환) | localhost 기본값 |
| `ANTHROPIC_API_KEY` | Anthropic SDK | 해당 경로 비활성 |
| `AWS_ACCESS_KEY_ID/SECRET/SESSION_TOKEN` | Bedrock/S3 자격증명 | `~/.aws` 폴백 |
| `BEDROCK_MODEL_ID` | LLM 모델 ID(inference profile 권장) | 기본 Claude 3.5 Sonnet |
| `BEDROCK_KNOWLEDGE_BASE_ID` | RAG 단락 조회 | RAG 생략(빈 단락) |
| `DOC_INDEX_S3_BUCKET` / `BEDROCK_KB_DATA_SOURCE_ID` | 시맨틱 인덱싱·ingestion | 인덱싱 no-op |
| `TRACE_BACKFILL_MIN_CONFIDENCE` | 백필 링크 생성 임계 점수 | 0.4 |
| `DOCUMENTS_DIR` | 업로드 문서 저장 디렉터리 | `./uploaded_documents` |
| `CODEWHY_TEAM_MAP` | 작성자→팀 매핑 JSON 경로 | team 칸 생략 |
| `GITHUB_TOKEN` / `GITLAB_TOKEN` | PR/MR 조회 | PR 연동 생략 |

> **설계 미덕**: 거의 모든 외부 연동(KB·S3·PR·팀맵)이 미설정 시 *no-op/폴백*으로 동작 → 로컬에서 일부 기능만으로도 깨지지 않음.

### DB 마이그레이션
```bash
cd backend && alembic upgrade head        # 스키마 적용
alembic revision --autogenerate -m "..."  # 스키마 변경 시
```

---

## 9. TODO 리스트 (코드에 박힌 미완성/검증 항목)

### 🔴 블로커 — 기능 동작에 필수
- [ ] **`timeline/service.py::compute_commit_set_hash()` 구현** — 현재 `NotImplementedError`. 타임라인 캐시 키(SHA-256). 커밋 해시 목록 정렬 → join → sha256 권장.

### 🟡 품질/정확도 — 구현은 됐으나 개선·검증 필요
- [ ] **`onboarding/backfill.py::build_retrieval_query()`** — 현재 커밋 메시지 키워드만 사용(baseline). 변경 파일명(camel/snake 분해) 신호를 더해 매칭 품질 향상.
- [ ] **Bedrock KB score 정규화 확인** — `_link_passages`/`_by_semantic`의 threshold(0.4)가 실제 score 스케일(0~1)과 맞는지 실데이터로 검증.
- [ ] **사이드카 메타데이터 포맷 검증** (`doc_index.index_document`) — `{"metadataAttributes": {"documentId": ...}}` 단순형 vs 상세형(NUMBER/includeForEmbedding=false) 중 KB 버전이 요구하는 형태 확인.
- [ ] **`documentId` 역추출 키 검증** (`knowledge_base._extract_document_id`) — retrieve 응답에서 커스텀 메타데이터가 실제로 어떤 키로 평탄화돼 오는지 확인 후 후보 키 보강.
- [ ] **`on_conflict_do_nothing` rowcount 정확성** (`_link_passages`) — asyncpg에서 삽입 1/스킵 0이 정확히 오는지 확인, 부정확하면 RETURNING으로 판정.
- [ ] **시맨틱 폴백 노출 정책** (`traceability._by_semantic`) — 약한 매칭(낮은 score)을 "추정"으로 보여줄지 최소 점수로 거를지 결정.

### 🟢 선택 — 비용/성능 최적화
- [ ] 역추적 시맨틱 폴백 결과 캐시 (blame처럼 file_id/line_no/commit_id 키) — 매 조회 KB 호출 부담 완화.
- [ ] 타임라인 map 단계 Bedrock 호출 **병렬화** (LangGraph `Send()` API).
- [ ] 백필 임계 구간(threshold 근처)만 LLM으로 excerpt 보강 (비용 통제).

---

## 10. 앞으로 고려해야 할 것

### 보안 🔐
- [ ] **README에 노출된 운영 정보 제거** — RDS 호스트 IP(`3.37.125.200`), DB 계정/비번(`postgres/postgres`), AWS 접속 절차가 평문으로 커밋돼 있음. 비밀번호 교체 + README에서 분리(팀 내부 채널/시크릿 매니저로).
- [ ] CORS가 `allow_origins=["*"]` — 배포 시 확장 origin으로 제한 검토.
- [ ] 문서 업로드 검증 부재 — 파일 크기/확장자/MIME 화이트리스트, 경로 traversal 방지.

### 안정성/완성도
- [ ] **미커밋 작업 정리** — `onboarding/`, `doc_index.py`, `0002_backfill_trace.py`를 리뷰 후 커밋. `.env.example`/`config.py`/`models.py` 등 수정분도 함께 정합성 확인.
- [ ] **테스트** — 현재 `src/test/extension.test.ts` 스켈레톤만 존재. 백엔드 단위 테스트(특히 `extract_keywords`, `extract_ticket`, 타임라인 파이프라인, 3단계 trace) 부재.
- [ ] **에러 응답 표준화** — 현재 기능별로 `HTTPException(500, f"...: {e}")` 패턴. 공통 에러 스키마/로깅 정비.
- [ ] **README ↔ 코드 정합** — 캐시(DynamoDB→PostgreSQL), 환경변수 목록, 온보딩 기능 반영.

### 배포/운영
- [ ] `backend/Dockerfile` 존재 — 배포 파이프라인(CI), 마이그레이션 자동 실행 전략 정리.
- [ ] 확장 패키징/배포는 `PACKAGING_GUIDE.md` 참고 — VSIX 빌드/게시 흐름 점검.
- [ ] Bedrock 호출 비용/레이트리밋 모니터링, ingestion job 상태 추적.
- [ ] 업로드 문서 저장소를 로컬 디스크 → S3로 일원화 검토(현재 원본은 `DOCUMENTS_DIR`, 인덱싱본은 S3로 이원화).

### 기능 확장 아이디어
- [ ] 역추적 결과를 확장 사이드바에서 바로 미리보기(PDF 페이지 점프).
- [ ] 블레임/타임라인/역추적 간 상호 내비게이션(같은 커밋·티켓으로 연결).
- [ ] 다국어/모노레포·서브모듈 레포 경로 처리.

---

## 11. 기여 시 참고

- **확장에 기능 추가**: `src/features/<기능>/` 폴더에 캡슐화하고 `src/extension.ts`에 register 호출만 추가.
- **백엔드에 기능 추가**: `features/<기능>/{router,service,schemas}.py` 구성 → `main.py`에 `include_router`.
- **공유 데이터**(commit/file/repo)는 `db/crud_common.py`의 upsert 헬퍼 재사용.
- **스키마 변경**은 ORM 수정 후 Alembic autogenerate.
- **외부 연동 추가** 시 미설정 환경에서 no-op/폴백하도록 작성(로컬 개발 보호).
```
