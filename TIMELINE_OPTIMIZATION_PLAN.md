# 타임라인 일괄 분석 폐지 + 캐시 노이즈 면제 계획 (A1 + C)

> 작성일: 2026-06-07 (최초)
> 갱신: 2026-06-07 (블레임 통일 관점 반영 — §2 C 분류기 위치 확정, §7 부록 신설)
> 대상 기능: Timeline Summary (`backend/app/features/timeline/`, `backend/app/features/project/`)
> 트리거: 서버가 일괄적으로 모든 소스를 스캔해 LLM으로 요약·DB insert하는 동작이 비용/속도 면에서 비효율적이라는 지적
> 관련 문서:
> - `DEVELOPMENT_GUIDE.md` §6 "공통 처리 원칙 (블레임 ↔ 타임라인)" — 다섯 원칙
> - `DEVELOPMENT_GUIDE.md` §10 인프라 "공통 커밋 분류기 추출" — 본 plan과 같은 모듈을 만든다
> - `DEVELOPMENT_GUIDE.md` §10 블레임 P1 "노이즈 커밋 LLM 우회" — 본 plan 완료 후 블레임이 같은 모듈을 import

---

## 1. Context — 비효율의 실체

서버 **시작 시 자동 스캔은 없다**(`main.py` lifespan은 DB ping만 수행). 비용을 만드는 건 두 엔드포인트와 캐시 키 설계 한 곳이다.

### 진짜 원인 두 가지

1. **eager 일괄 분석 (Bedrock 곱셈)**
   - `POST /api/timeline/files/analyze` → `analyze_all_project_files` 백그라운드 태스크
     - 위치: `backend/app/features/timeline/router.py:37-53`, `backend/app/features/timeline/tasks.py`
   - `POST /api/v1/project/initialize` → `analyze_files_timeline_task` 백그라운드 태스크
     - 위치: `backend/app/features/project/router.py:49-56`, `backend/app/features/project/tasks.py`
   - 동작: `git ls-files`로 추적 파일을 모두 수집(`_SOURCE_EXTENSIONS` 필터) → 파일마다 캐시 검사 후 미스면 Bedrock 1회.
   - 문제: 사용자가 끝내 열어보지 않을 파일까지 LLM 비용이 발생한다.

2. **노이즈 커밋이 캐시를 무력화**
   - `compute_commit_set_hash(commits)`는 파일의 **모든 커밋 해시**를 정렬해 SHA-256으로 묶는다.
     - 위치: `backend/app/features/timeline/service.py:21-27`
   - 반면 LangGraph는 LLM 호출 직전에 `_SKIP_TYPES = {"test", "chore", "docs"}` 타입을 거른다.
     - 위치: `backend/app/features/timeline/graph.py:48-78`
   - 결과: docs/test/chore 커밋 한 줄에도 캐시는 무효화되어 Bedrock이 다시 호출되지만, LangGraph 안에서 어차피 필터되므로 결과는 사실상 동일하다. **헛 호출**.

### 목표

- 일괄 분석 경로를 완전히 제거 → 사용자가 실제로 여는 파일만 분석(lazy on-demand).
- 캐시 키가 노이즈 커밋에 의해 무효화되지 않도록 한 함수만 수정.
- 마이그레이션 없이 자연 재계산으로 흡수.

---

## 2. 수정 대상

### A1. 일괄 분석 경로 제거 (Backend; 프론트는 `/files/analyze`를 호출하지 않음을 확인)

#### 제거할 엔드포인트

| 파일 | 변경 |
|---|---|
| `backend/app/features/timeline/router.py` | `POST /files/analyze` 핸들러(37-53행), `ProjectAnalyzeRequest`/`ProjectAnalyzeResponse` 모델, `from .tasks import analyze_all_project_files` import 제거 |
| `backend/app/features/project/router.py` | `POST /initialize`의 `background_tasks.add_task(analyze_files_timeline_task, ...)`(56행) 제거. 초기화가 분석 외에 메타 처리(예: repo_id 확보)를 한다면 그 부분만 유지하고 분석 트리거만 끊는다 |

#### 제거할 모듈 (다른 호출처가 없는지 grep 재확인 후 삭제)

- `backend/app/features/timeline/tasks.py` — `analyze_all_project_files` 등 일괄 분석 함수 전부
- `backend/app/features/timeline/file_summary.py` — `analyze_and_save_file_summary` (LLM 호출 지점, 100-229행)
- `backend/app/features/project/tasks.py` — `analyze_files_timeline_task` 및 의존 헬퍼(`_get_tracked_files`, `_FileAnalysis` 등). 파일 전체 삭제가 안전한지 한 번 더 확인
- `backend/app/ai/timeline_file_graph.py` — `timeline/file_summary.py`와 `project/tasks.py` 두 곳에서만 사용. 두 곳이 모두 제거되면 함께 삭제

#### 유지·대체 흐름

- 사용자가 특정 파일의 타임라인을 열면 VSCode 확장이 `POST /api/timeline/summary`를 호출한다.
- `backend/app/features/timeline/service.py:30-48`이 캐시 조회 → 미스 시 LangGraph(`backend/app/features/timeline/graph.py`) 1회 실행.
- 이 경로는 **이미 구현되어 있고 변경 불필요**.
- `GET /api/v1/project/timeline`이 화면에 리스트를 그리는 용도라면, 캐시에 없는 파일은 "분석 전" 상태로 반환하고 클릭 시점에 `/summary`가 채우는 방식으로 정렬한다. 응답 스키마 변경이 필요한지는 라우터의 현재 응답을 보고 결정(라이트 변경이면 본 작업에 포함, 크면 별도 작업으로 분리).

### C. 노이즈 커밋을 캐시 키 계산에서 제외 (+ 공통 분류기 모듈 신설)

> **블레임과의 통일을 위해 분류기는 `timeline/` 안이 아니라 `core/`에 둔다.** 블레임 `analyze_blame`이 같은 정의를 import해 노이즈 우회를 적용할 예정이기 때문(`DEVELOPMENT_GUIDE.md` §10 블레임 P1 참고). `timeline/_commit_classifier.py`로 두면 블레임이 timeline의 내부를 import해야 해서 모듈 경계가 깨진다.

**신규 파일**: `backend/app/core/commit_classifier.py`

```python
"""커밋 메시지 분류기 — 블레임/타임라인 공유.

타임라인의 캐시 키 계산(이 plan의 C)과 블레임의 노이즈 우회(DEVELOPMENT_GUIDE §10 P1)가
같은 정의를 보고 동작하도록 한 곳에서 관리한다.
"""

import re
from typing import Iterable

SKIP_TYPES: frozenset[str] = frozenset({"test", "chore", "docs"})

_COMMIT_RE = re.compile(r"^(?P<type>\w+)(?:\[(?P<domain>[^\]]+)\])?:\s*(?P<subject>.+)$")


def classify_commit(commit: dict) -> dict:
    """{type, domain, subject}를 추가해 돌려준다. 매칭 실패 시 type='other'."""
    m = _COMMIT_RE.match(commit.get("subject") or commit.get("message", ""))
    if not m:
        return {**commit, "type": "other", "domain": None}
    return {**commit, "type": m.group("type").lower(), "domain": m.group("domain")}


def filter_meaningful(commits: Iterable[dict]) -> list[dict]:
    """SKIP_TYPES(test/chore/docs)를 제외한 커밋만 반환. 전부 노이즈면 전체를 그대로 돌려준다."""
    classified = [classify_commit(c) for c in commits]
    meaningful = [c for c in classified if c["type"] not in SKIP_TYPES]
    return meaningful or classified
```

**변경 대상 파일**

1. `backend/app/features/timeline/graph.py`
   - 내부의 `_classify_commit`, `_SKIP_TYPES` 정의 제거
   - `from app.core.commit_classifier import classify_commit, SKIP_TYPES, filter_meaningful` import
   - `classify_and_split`, `map_summarize` 등 내부 호출처를 새 함수 이름으로 일괄 치환
   - 동작은 동등. 회귀 없음

2. `backend/app/features/timeline/service.py` — `compute_commit_set_hash` 수정

```python
import hashlib
from app.core.commit_classifier import filter_meaningful

def compute_commit_set_hash(commits: list[dict]) -> str:
    target = filter_meaningful(commits)
    serialized = "\n".join(sorted(c["hash"] for c in target))
    return hashlib.sha256(serialized.encode()).hexdigest()
```

**왜 `core/`인가**
- 블레임(`features/blame/service.py::analyze_blame`)이 본 작업 완료 후 같은 분류기를 import한다. `features/timeline/_commit_classifier.py`로 두면 다른 feature가 `features/timeline/` 내부 모듈에 의존하게 되어 모듈 경계가 깨진다.
- `core/`에 두면 추후 역추적·온보딩이 같은 필터링을 도입할 때도 그대로 재사용 가능.

**순환 import 우려 없음**: `core/commit_classifier.py`는 다른 내부 모듈을 import하지 않는다(re, typing만 사용). `features/*`가 단방향으로 의존한다.

---

## 3. 영향 분석

| 영역 | 변화 |
|---|---|
| 첫 사용 UX | 한 파일을 처음 여는 순간 수 초 대기. 그 이후 같은 파일은 캐시로 즉시 응답 |
| Bedrock 비용 | 안 열린 파일 0건, 노이즈만 푸시된 갱신 0건 |
| DB 부하 | 일괄 INSERT/UPDATE 사라짐 |
| 기존 캐시 행 | 새 해시 함수에서 1회 미스 → 재계산 후 안정. 마이그레이션 불필요 |
| 코드량 | `timeline/tasks.py`, `timeline/file_summary.py`, `project/tasks.py`(또는 일부), `ai/timeline_file_graph.py` 제거 / `core/commit_classifier.py` 신설 |
| 다른 기능 영향 | 블레임이 본 plan 완료 후 `core/commit_classifier.py`를 import해 노이즈 우회 적용 — 시그니처(`SKIP_TYPES`, `classify_commit`, `filter_meaningful`)는 호환 유지 필요 |
| DEVELOPMENT_GUIDE.md | §6 "공통 처리 원칙"과 §10 "공통 커밋 분류기 추출" 항목에 이미 반영됨 (2026-06-07 갱신) |

---

## 4. 위험 / 미결 사항

- `features/project/`가 타임라인 분석 외에 가치 있는 메타 동작을 하는지 확인 필요. 단순 트리거 래퍼였다면 라우터·태스크·스키마 전체 삭제 가능. 그렇지 않다면 분석 트리거만 끊고 나머지는 유지.
- `GET /api/v1/project/timeline` 응답 스키마가 "모든 파일이 미리 채워져 있다"를 가정하는지 확인. 가정이 있다면 응답 변형(분석 안 됨 상태 추가) 또는 호출자(프론트) 변경이 동반될 수 있음.
- 위 두 항목은 실제 코드를 더 보고 부록으로 결정.

---

## 5. 검증 체크리스트

1. `POST /api/timeline/files/analyze` 호출 시 404 응답 (엔드포인트 부재 확인)
2. `POST /api/v1/project/initialize`가 즉시 반환되며 백그라운드 Bedrock 호출 0건 (로그/모니터링으로 확인)
3. `POST /api/timeline/summary` 캐시 미스 파일 → LangGraph 1회 실행 → `{summary, milestones}` 정상
4. 같은 파일에 docs/test/chore 커밋만 추가 후 `/summary` 재호출 → **캐시 적중, LLM 0회**
5. 같은 파일에 feat 커밋 추가 후 `/summary` 재호출 → 캐시 미스, LLM 1회, 새 summary 반환
6. 사전에 일괄 분석으로 채워졌던 `timeline_summaries` 행이 새 해시에서 1회 재계산 후 정상 응답되는지 확인
7. 백엔드 로그에 일괄 분석 관련 로그(`analyze_project_files`, `analyze_all_project_files`)가 더 이상 출력되지 않음
8. **분류기 추출 검증**: `backend/app/core/commit_classifier.py`가 존재하고, `features/timeline/graph.py`와 `features/timeline/service.py`가 그것만 import (각 파일 안의 `_classify_commit`/`_SKIP_TYPES` 정의가 모두 제거됨)
9. **분류기 동작 검증**: `classify_commit({"subject": "docs[readme]: 환경변수 보완"})` → `type == "docs"`, `filter_meaningful([…])`가 docs/test/chore 제외 (간단 pytest 또는 REPL)
10. **블레임 통일 준비 확인**: `from app.core.commit_classifier import classify_commit, SKIP_TYPES, filter_meaningful`가 블레임 측에서 import 가능한 형태로 노출되어 있는지 (이름·시그니처 변경은 본 plan 안에서 확정)

---

## 6. 다른 선택지와의 비교 (왜 A1+C인가)

지난 논의에서 후보로 올렸던 옵션들:

| 옵션 | 절감 | 구현 복잡도 | 채택 여부 |
|---|---|---|---|
| **A1 일괄 분석 완전 폐지** | ★★★★★ | ★ | ✅ 채택 |
| A2 일괄 분석 옵션화(좁은 범위) | ★★★★ | ★ | ✗ — A1이 더 깔끔 |
| B 증분 요약(기존 summary + 신 커밋) | ★★★★ | ★★★ | ✗ — 스키마 변경 부담, 향후 별도 작업 |
| **C 노이즈 커밋 캐시 면제** | ★★ | ★ (한 함수) | ✅ 채택 |
| D 소규모 파일 Map-Reduce 우회 | ★★ | ★ | △ — 후속 작업으로 미룸 |
| E 비용 가드레일 | (사고 방지) | ★★ | △ — A1 정착 후 |

A1은 안 열어볼 파일에 대한 LLM 호출 자체를 없애 절감 폭이 가장 크고, C는 한 함수 수정으로 노이즈 캐시 무효화를 막는다. 둘 다 마이그레이션이 없고 UX 회귀 위험이 낮다.

---

## 7. 블레임과의 통일 작업 부록 (담당자에게 전달)

본 plan은 타임라인만의 작업이 아니다. **블레임도 같은 비용·속도 원칙을 따라야** 운영 일관성이 유지된다(`DEVELOPMENT_GUIDE.md` §6 "공통 처리 원칙"). 본 plan을 진행하는 담당자는 아래 작업 순서를 인지하고, **§2 C의 `core/commit_classifier.py` 시그니처를 다른 기능과 합의된 형태로 확정**해 두어야 한다.

### 7.1 작업 순서 (전체 흐름)

| # | 작업 | 책임 | 비고 |
|---|---|---|---|
| 1 | `core/commit_classifier.py` 신설 (본 plan §2 C) | 타임라인 담당 | 시그니처 변경 금지 — 블레임이 동일 모듈 import |
| 2 | `timeline/graph.py`·`timeline/service.py`가 새 모듈 import (본 plan §2 C) | 타임라인 담당 | |
| 3 | `timeline/tasks.py` 등 일괄 분석 모듈 제거 (본 plan §2 A1) | 타임라인 담당 | |
| 4 | 블레임 `analyze_blame`에 노이즈 우회 추가 | 블레임 담당 (별도 작업) | `from app.core.commit_classifier import ...` |
| 5 | `core/vcs.py` PR/Issue 조회 메모이즈 | 블레임 담당 (별도 작업) | `DEVELOPMENT_GUIDE.md` §10 P1 |

1~3은 본 plan의 범위, 4~5는 블레임 담당의 후속 작업이다. **1~3 작업 시점에 4번이 빌드될 수 있는 형태로 분류기 모듈을 노출**하는 것이 본 plan의 책임이다.

### 7.2 분류기 모듈 계약 (변경 금지)

블레임이 본 모듈을 import해 다음과 같이 쓸 예정이다. 본 plan 진행 중 다음 시그니처를 깨면 블레임 작업이 막힌다.

```python
# 블레임 측 예상 사용 (참고용)
from app.core.commit_classifier import classify_commit, SKIP_TYPES

info = git.get_blame_info(...)
commit_type = classify_commit({"subject": info.message})["type"]
if commit_type in SKIP_TYPES:
    return "[자동 분류] 문서/테스트/설정 정비 커밋입니다."   # Bedrock 호출 없이 정형 응답
# 그 외 경로: 기존 analyze_blame
```

**고정해야 할 계약**
- 모듈 경로: `app.core.commit_classifier`
- 공개 심볼: `classify_commit(commit: dict) -> dict`, `SKIP_TYPES: frozenset[str]`, `filter_meaningful(commits) -> list[dict]`
- `classify_commit` 반환 dict의 `type` 키는 소문자 문자열(`"feat" | "fix" | "docs" | …`)
- `SKIP_TYPES`는 최소 `{"test", "chore", "docs"}` 포함

이름이나 시그니처를 바꿔야 한다면 블레임 담당자와 합의 후 본 plan의 §7.2도 함께 갱신한다.

### 7.3 본 plan이 책임지지 않는 것

- 블레임의 `analyze_blame` 코드 변경 — 블레임 담당자의 별도 작업
- `core/vcs.py` PR/Issue 조회 메모이즈 — 블레임 담당자의 별도 작업
- 역추적 기능의 분류기 도입 — 역추적 담당자의 향후 작업

이들은 모두 본 plan이 만든 `core/commit_classifier.py` 위에 빌드되므로, **본 plan은 그 토대를 안전하게 깔아둔다는 책임**까지가 범위다.
