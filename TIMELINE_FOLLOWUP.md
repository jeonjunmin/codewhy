# 타임라인 후속 개발 항목 (성능 + 정합성)

> 작성일: 2026-06-07
> 선행 문서: `TIMELINE_OPTIMIZATION_PLAN.md` (A1 + C 적용 결과 검토)
> 대상: `backend/app/features/timeline/`, `backend/app/features/project/`, `backend/app/ai/`
> 작성 이유: A1(일괄 분석 폐지)·C(노이즈 캐시 면제) 적용 후 **잔여 이슈 + 핫패스 비효율 + 버그**를 정리해 다음 담당자에게 인계

---

## 0. 한눈에 요약

| 분류 | 항목 | 영향 | 우선순위 |
|---|---|---|---|
| 🐛 버그 | `project/router.py:116` `folder_name` 미정의 → `/timeline` 500 | 정상 흐름 마지막에 NameError | **P0** |
| ⚠️ 정합성 | `ai/graph.py`에 `_SKIP_TYPES`/`_classify_commit` 잔존 (계획 §2 C 미완) | SSOT 깨짐, 향후 노이즈 추가 시 두 곳 모두 수정 필요 | **P0** |
| 🐌 성능 | 캐시 적중 경로에서도 매 요청 N+1 upsert | 핫패스 RT 증가, DB 쓰기 부담 | **P1** |
| 🐌 성능 | `get_cached_summary` 캐시 미스 시 디버그 쿼리 1회 추가 | 운영에서 불필요한 쿼리 | **P2** |
| 🧹 정리 | `ai/graph.py`, `ai/project_graph.py` 호출처 0 — dead code | 혼동, 분류기 중복의 원인 | **P1** |
| 💡 UX | lazy on-demand 전환 후 첫 파일 진입 시 3~8초 대기 | 안내 UI 없으면 멈춘 듯 보임 | **P2** |

---

## 1. 처리 속도 분석

### 1.1 캐시 적중 경로 (`POST /api/timeline/summary`, 정상 hot path)

`backend/app/features/timeline/service.py::summarize` 흐름을 따라가면:

| 단계 | 코드 | 쿼리/호출 | 비고 |
|---|---|---|---|
| ① upsert_commits | `crud.py:29` | repo `get_or_create` 1회 + file `get_or_create` 1회 + **커밋 N개 × (upsert + link)** ≈ 2N+2 statement | 캐시 적중에도 매번 수행 |
| ② `await db.commit()` | `crud.py:54` | 트랜잭션 1회 | 위 N+1 모두 flush |
| ③ get_commits | `crud.py:58` | JOIN 1회 (LIMIT 200) | 정상 |
| ④ compute_commit_set_hash | `service.py:30` | 0 — 메모리 연산 | OK |
| ⑤ get_cached_summary | `crud.py:84` | 1회 (히트 시 끝) | 정상 |

**문제**: 캐시 키 비교 전에 **항상** N+1 DB 쓰기가 일어난다. 사용자가 같은 파일을 두 번 열면 두 번 다 2N+2 statement가 흘러간다. 캐시의 의미가 LLM 비용 절감에만 한정되고 DB 부하에는 미치지 않는다.

**개선 방향**:
1. (단기) `upsert_commit`을 **bulk upsert** 한 번으로 묶기 — `INSERT ... ON CONFLICT DO NOTHING`을 N행 한 번에. `crud_common`에 `upsert_commits_bulk(repo_id, commits)`를 추가하고 `link_commits_files_bulk(commit_ids, file_id)`로 commit_files도 일괄 처리.
2. (중기) 클라이언트가 보낸 `commits` 해시 셋이 **DB의 마지막 N건과 동일하면 upsert 자체를 스킵**. 예: `get_commits(limit=len(req_commits))`로 최신 N개를 가져와 hash set 비교 → 일치하면 ① 전체 우회.
3. (장기) `commits` 동기화를 `/summary`에서 분리. 확장이 별도 `POST /api/v1/commits/sync`로 백그라운드 동기화하고, `/summary`는 읽기 전용 hot path가 되도록.

### 1.2 캐시 미스 경로

추가 비용:
- `_get_file_diff` — `git show HEAD -- <file>` subprocess, `asyncio.to_thread` 위임 (OK). 단 **항상 HEAD만** 보므로 과거 변경은 prompt 텍스트로만 들어감 — 정보 손실 가능성.
- `run_file_timeline_graph` — Bedrock 단일 호출. 평균 3~8초.
- `save_summary` — `ON CONFLICT DO UPDATE` 1회. OK.

**측정 권장**: `service.py`에 단계별 `time.perf_counter()` 찍어서 hot path 캐시 적중/미스 별 p50·p95 분리 로깅. 현재 로그는 정성적이라 수치 추적이 어려움.

### 1.3 `GET /api/v1/project/timeline`

`project/router.py:62`:
- repo 조회 1 + files 조회 1 + summaries 조회 1 = 3 쿼리. N+1 없음.
- `file_ids.in_(...)` — 파일 수가 수천 단위가 되면 IN 절 크기 우려. 현재 규모에선 무시 가능.
- **마지막 줄에 NameError 버그** (§2.1 참조).

### 1.4 LangGraph 측

`ai/timeline_file_graph.py`는 **단일 노드 단일 Bedrock 호출** — 빠름. 정상.

`ai/graph.py`는 **Map-Reduce 파이프라인** (청크 N개 map + reduce + 재시도)이라 호출이 N+1회. 만약 살아있다면 비용/속도 모두 나쁘지만 **현재 호출처 0** (§3.3 참조).

---

## 2. P0 — 즉시 수정 필요

### 2.1 `project/router.py:116` NameError

```python
# 현재
logger.info("timeline: %s → %d개 파일 반환", folder_name, len(result))
```

`folder_name`은 함수 어디에도 정의되지 않음. `/timeline`이 정상 데이터를 반환하기 직전에 `NameError`로 500.

**수정안** (택1):
```python
logger.info("timeline: %s → %d개 파일 반환", project_path, len(result))
# 또는
logger.info("timeline: %s → %d개 파일 반환", os.path.basename(project_path), len(result))
```

운영 로그에서 더 유용한 표현을 택하면 됨. 기존 변수명이 `folder_name`이었다는 점에서 후자(basename)가 원래 의도로 보임.

### 2.2 `ai/graph.py` 분류기 통일 (계획 §2 C 미완)

`backend/app/ai/graph.py`에 잔존:
- L49 `_SKIP_TYPES = {"test", "chore", "docs"}`
- L68 `def _classify_commit(commit: dict) -> dict:`
- L103-104 호출처

**문제**: `app/core/commit_classifier.py`(service.py가 사용)와 정의가 두 곳으로 나뉜다. 향후 `SKIP_TYPES`에 `style`을 추가하면 service.py만 반영되고 graph.py는 누락되는 식의 사고가 난다. 계획서 §7.2 "계약(변경 금지)"의 SSOT 원칙 위반.

**해결안**:
- **(A) 분류기 import로 교체** — 분류기 모듈을 그대로 사용:
  ```python
  from app.core.commit_classifier import classify_commit, SKIP_TYPES
  # _SKIP_TYPES, _classify_commit 정의 삭제
  # L103-104 호출처를 새 이름으로 치환
  ```
  단, `_classify_commit`은 `description` 키도 만들어 반환하는데, 공용 분류기는 그렇지 않음 → graph.py 내 호출처에서 `description`이 필요하면 호출 직후 직접 만들거나, 공용 분류기에 `description`을 추가해야 함.

- **(B) 파일 삭제** — `run_timeline_graph`이 어디에서도 호출되지 않음(§3.3). graph.py 전체를 삭제하면 분류기 중복도 자동 해결.

**권장**: (B). dead code 삭제가 더 빠르고 깨끗하다. 다만 정말 호출처가 없는지 한 번 더 grep 확인 후 진행.

---

## 3. P1 — 다음 스프린트에 권장

### 3.1 캐시 적중 hot path 단축 (성능)

§1.1의 N+1 upsert가 캐시의 효용을 절반만 살리고 있음. 두 가지 방향:

**(a) Bulk upsert로 1차 단축**

`crud_common`에 다음 추가:
```python
async def upsert_commits_bulk(
    db: AsyncSession, repo_id: int, commits: list[dict]
) -> dict[str, int]:
    """commit_hash → commit_id 매핑을 반환. 1개 SQL로 N행 처리."""
    # INSERT ... ON CONFLICT (repo_id, commit_hash) DO UPDATE ... RETURNING id, commit_hash
    ...

async def link_commits_files_bulk(
    db: AsyncSession, commit_ids: list[int], file_id: int
) -> None:
    """commit_files 다중 INSERT (ON CONFLICT DO NOTHING)."""
    ...
```
`timeline/crud.py::upsert_commits`를 이 두 호출로 압축. ~2N+2 statement → 2 statement.

**(b) 동기화 자체 우회 (요청-시점 비교)**

확장이 보낸 커밋 해시 셋이 DB 최신 N건과 동일하면 ① 전체 스킵. 예시:
```python
# upsert 직전
req_hashes = {c["hash"] for c in commits}
recent = await crud.get_recent_hashes(db, file.id, limit=len(req_hashes))
if req_hashes == set(recent):
    pass  # 동기화 불필요
else:
    await crud_common.upsert_commits_bulk(...)
```
파일을 같은 시점에 두 번째 열면 hot path가 read-only가 됨.

**권장**: (a)를 먼저, (b)는 측정 후 결정.

### 3.2 Dead code 삭제

호출처 0 확인됨 (`grep -r "run_timeline_graph\|from app.ai.graph\|project_graph"` 결과 self-reference만):
- `backend/app/ai/graph.py` — 전체 삭제
- `backend/app/ai/project_graph.py` — 전체 삭제

삭제 전 한 번 더 `git grep`으로 확인 권장. 삭제 시 §2.2 (A1+C 통일)이 자동 해결됨.

---

## 4. P2 — 여유가 될 때

### 4.1 `get_cached_summary` 디버그 쿼리 제거 (성능)

`crud.py:96-101`:
```python
if row is None:
    all_rows = (await db.execute(
        select(TimelineSummary.commit_set_hash).where(TimelineSummary.file_id == file_id)
    )).scalars().all()
    logger.info("[crud] DB 내 file_id=%d 요약 해시 목록: %s", ...)
```

캐시 미스마다 추가 쿼리 1회. 개발 중 노이즈 캐시 무효화 디버깅 목적이었다면, 이제 §1.1의 C 적용으로 노이즈가 거의 안 생기므로 제거하거나 `logger.debug` (DEBUG 레벨에서만) 로 강등.

### 4.2 첫 진입 UX 보강

lazy 전환 후 한 파일을 처음 열 때 3~8초 무응답 구간이 생김. VSCode 확장 쪽에서:
- 진행 표시 (StatusBar item 또는 webview placeholder)
- 동시 호출 가드 (같은 파일에 대해 in-flight 요청이 있으면 dedup)

백엔드는 변경 없음. 확장 담당자에게 전달.

### 4.3 `_get_file_diff`의 정보 범위 검토

현재 `git show HEAD -- <file>`만 본다 (`service.py:42`). 캐시 키는 **전체 커밋 셋의 해시**인데 LLM이 보는 diff는 최신 1개. 다음 둘이 다른 결과를 만든다:
- 파일 A: feat 5건 누적 → 캐시 미스 → HEAD diff만 보고 요약
- 파일 B: 동일 5건이지만 다른 순서로 푸시 → 캐시 미스 → 다른 HEAD diff로 다른 요약

의도된 단순화(LLM은 commits_text도 함께 받음)인지, plan B "증분 요약"으로 가야 하는지 product 결정 필요. 현 상태에서는 commits_text가 fallback이고 diff_text가 우선이라 사실상 HEAD에 무게중심이 쏠림. **체크포인트만 박아두고** plan B로 별도 작업화 권장.

---

## 5. 검증 체크리스트 (작업 완료 후)

P0:
- [ ] `GET /api/v1/project/timeline?project_path=...`이 정상 200 반환 (NameError 미발생)
- [ ] `grep -r "_SKIP_TYPES\|_classify_commit" backend/app/` 결과가 `core/commit_classifier.py` 외에 0건

P1:
- [ ] `POST /api/timeline/summary` 캐시 적중 시 INSERT/UPDATE statement 수가 (a) 적용 후 ≤ 2, (b) 적용 후 0
- [ ] `backend/app/ai/graph.py`, `backend/app/ai/project_graph.py` 부재 (rm 후 import 에러 없음)
- [ ] `pytest` 또는 수동 호출로 동일 파일 2회 `/summary` → 2회 모두 동일 응답, 2회차는 RT ≤ 100ms

P2:
- [ ] `get_cached_summary` 캐시 미스 시 추가 디버그 쿼리 미발생 (또는 DEBUG 레벨로만)
- [ ] (확장 담당) 첫 진입 시 로딩 표시 확인
- [ ] (PM 확인) `_get_file_diff` 범위 정책 결정 — 현 상태 유지 vs plan B 진행

---

## 6. 인계 시 주의 (계약 보존)

`app/core/commit_classifier.py`는 블레임 담당자가 동일 모듈을 import할 예정 (`TIMELINE_OPTIMIZATION_PLAN.md §7.2`). **다음 시그니처는 절대 변경 금지**:
- 모듈 경로: `app.core.commit_classifier`
- 공개 심볼: `classify_commit(commit: dict) -> dict`, `SKIP_TYPES: frozenset[str]`, `filter_meaningful(commits) -> list[dict]`
- `classify_commit` 반환 dict의 `type` 키는 소문자 문자열
- `SKIP_TYPES`는 최소 `{"test", "chore", "docs"}` 포함

§2.2에서 `_classify_commit`이 만들던 `description` 키가 필요해 공용 분류기에 추가하더라도, **기존 키(`type`, `domain`)는 그대로 유지**할 것.
