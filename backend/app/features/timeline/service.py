"""Timeline Summary 비즈니스 로직.

데이터 흐름:
  ① 확장이 보낸 commits
  → ② PostgreSQL 백본에 upsert + 파일 전체 이력 조회 (crud.py)
  → ③ commit_set_hash 로 요약 캐시 조회
       — 적중 시 prepare_summary() 가 즉시 결과를 반환 (router 가 JSON 응답)
       — 미스 시 prepare_summary() 가 스트리밍 컨텍스트를 반환 (router 가 SSE 스트림 응답)
  → ④ 미스일 때만: git diff 추출 → stream_summary() 가 Bedrock 토큰을
       SSE(`data: ...`) 프레임으로 실시간 전달하고, 스트림 종료 시점에 누적 텍스트를
       파싱해 timeline_summaries 캐시에 저장한다

👤 담당: 개발자 B
"""

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.timeline_file_graph import parse_ai_response, stream_file_summary
from app.core.commit_classifier import classify_commit, filter_meaningful
from app.core.tickets import extract_ticket
from app.features.timeline import crud

logger = logging.getLogger(__name__)

_COMMIT_RE = re.compile(r"^(?P<type>\w+)(?:\[(?P<domain>[^\]]+)\])?:")

# ── 마일스톤 그룹핑 튜닝 노브 ────────────────────────────────────────────────
# 한 묶음에 담을 최대 커밋 수 — 너무 큰 묶음이 '마일스톤' 의미를 잃지 않게 끊는다.
_MAX_GROUP_SIZE = 8
# 같은 묶음으로 볼 시간 간격(일) — 직전 커밋과 이만큼 벌어지면 새 마일스톤으로 분리.
_TIME_GAP_DAYS = 14
# 마일스톤(묶음) 개수 상한 — 넘으면 가장 오래된 묶음부터 합쳐 최근 해상도를 보존한다.
_MAX_MILESTONES = 5


def compute_commit_set_hash(commits: list[dict]) -> str:
    """파일의 커밋 목록으로부터 타임라인 요약 캐시 키(SHA-256 hex)를 계산한다.

    노이즈 커밋(test/chore/docs)은 LangGraph 진입 전에 이미 걸러지므로
    캐시 키 계산에서도 동일하게 제외한다 — 노이즈 커밋만 추가됐을 때
    캐시가 불필요하게 무효화되는 것을 방지한다 (TIMELINE_OPTIMIZATION_PLAN.md §2 C).
    """
    target = filter_meaningful(commits)
    serialized = "\n".join(sorted(c["hash"] for c in target))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _parse_commit(message: str) -> dict[str, str]:
    m = _COMMIT_RE.match(message.strip())
    if m:
        return {"type": m.group("type").lower(), "domain": (m.group("domain") or "").lower()}
    return {"type": "unknown", "domain": ""}


# ── 마일스톤 그룹핑 (결정론적 — LLM 없음) ─────────────────────────────────────

def _parse_ymd(value: str):
    """'YYYY-MM-DD' → datetime. 파싱 실패 시 None(같은 묶음으로 취급되도록)."""
    try:
        return datetime.strptime((value or "")[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _type_domain(commit: dict) -> tuple[str, str]:
    """커밋의 (type, domain) — 같은 작업 묶음 판정용. 공통 classify_commit 사용."""
    info = classify_commit({"subject": commit.get("subject", "")})
    return (info["type"], info.get("domain") or "")


# ─────────────────────────────────────────────────────────────────────────────
# 👉 사용자 작성 구역 — '마일스톤 경계' 판정 (담당: 개발자 B)
#
# 누적 중인 묶음(group, 오래된→최근 순)에 다음 커밋(cur)을 이어 붙일지,
# 아니면 cur 부터 '새 마일스톤'을 시작할지 결정한다. True 면 새 묶음 시작.
# 여기가 "수정 이력을 어떻게 묶을지"의 핵심 — 도메인 판단이 들어가는 5~10줄이다.
#
# 아래는 동작하는 기본값(추천: 티켓 우선)이다. 제품 성격에 맞게 우선순위를 조정하세요:
#   · 티켓 우선  → '기획 단위' 마일스톤 (같은 이슈 작업이 한 칸)  ← 현재 기본
#   · 시간 갭 우선 → '작업 시즌' 단위 마일스톤
# 끄고 싶은 조건은 줄을 지우면 되고, _MAX_GROUP_SIZE/_TIME_GAP_DAYS 로 강도를 조절한다.
# ─────────────────────────────────────────────────────────────────────────────
def _starts_new_milestone(group: list[dict], cur: dict) -> bool:
    anchor, prev = group[0], group[-1]

    # ① 크기 상한 — 한 묶음이 너무 커지면 끊는다.
    if len(group) >= _MAX_GROUP_SIZE:
        return True
    # ② 티켓 경계 — 묶음과 cur 이 서로 다른 이슈를 참조하면 분리(둘 다 티켓이 있을 때만).
    anchor_ticket = extract_ticket(anchor.get("subject", ""))
    cur_ticket = extract_ticket(cur.get("subject", ""))
    if anchor_ticket and cur_ticket and anchor_ticket != cur_ticket:
        return True
    # ③ 작업 종류 경계 — type/domain 이 바뀌면 다른 흐름으로 본다(feat[pay]→fix[auth] 등).
    if _type_domain(anchor) != _type_domain(cur):
        return True
    # ④ 시간 갭 — 직전 커밋과 _TIME_GAP_DAYS 넘게 벌어지면 새 시기로 분리.
    d_prev, d_cur = _parse_ymd(prev.get("date", "")), _parse_ymd(cur.get("date", ""))
    if d_prev and d_cur and (d_cur - d_prev).days > _TIME_GAP_DAYS:
        return True
    return False


def group_commits_into_milestones(commits: list[dict]) -> list[list[dict]]:
    """의미있는 커밋(최신순)을 '마일스톤' 묶음으로 가른다 — 결정론적(LLM 없음).

    입력: get_commits 형식 [{"hash","author","date","subject"}, ...] (최신순).
    반환: 묶음 리스트(오래된→최근 순). 각 묶음은 커밋 dict 리스트(오래된→최근).
          이후 LLM 이 묶음마다 '한 줄 라벨'만 생성한다.
    경계 규칙은 _starts_new_milestone, 개수 상한은 _coalesce_to_cap 이 담당한다.
    """
    chrono = list(reversed(commits))  # 오래된→최근 순으로 누적 그룹핑
    if not chrono:
        return []
    groups: list[list[dict]] = [[chrono[0]]]
    for cur in chrono[1:]:
        if _starts_new_milestone(groups[-1], cur):
            groups.append([cur])
        else:
            groups[-1].append(cur)
    return _coalesce_to_cap(groups, _MAX_MILESTONES)


def _coalesce_to_cap(groups: list[list[dict]], max_n: int) -> list[list[dict]]:
    """묶음 수가 상한을 넘으면 가장 오래된 두 묶음부터 합친다(최근 변화의 해상도 보존)."""
    while len(groups) > max_n:
        groups = [groups[0] + groups[1]] + groups[2:]
    return groups


def _group_date(group: list[dict]) -> str:
    """묶음의 대표 날짜 — 그 묶음의 마지막(가장 최근) 커밋 날짜."""
    return group[-1].get("date", "") if group else ""


def _group_period(group: list[dict]) -> str:
    """묶음 기간 표기 — 'YYYY-MM-DD ~ YYYY-MM-DD'(단일 날짜면 한 번만)."""
    start, end = group[0].get("date", ""), _group_date(group)
    return start if start == end else f"{start} ~ {end}"


def _format_groups_for_prompt(groups: list[list[dict]]) -> str:
    """묶음들을 LLM 프롬프트용 텍스트로 — 묶음마다 인덱스/기간/커밋 목록."""
    blocks: list[str] = []
    for i, group in enumerate(groups, 1):
        lines = "\n".join(
            f"  - [{c.get('date', '')}] {c.get('subject', '')} (by {c.get('author', '')})"
            for c in group
        )
        blocks.append(f"[묶음 {i}] ({_group_period(group)}, 커밋 {len(group)}개)\n{lines}")
    return "\n\n".join(blocks)


def _seed_milestones(groups: list[list[dict]]) -> list[dict]:
    """LLM 라벨이 없을 때 쓸 폴백 마일스톤 — 날짜는 결정론적, 라벨은 마지막 커밋 제목."""
    return [
        {"date": _group_date(g), "description": (g[-1].get("subject", "") or "").strip()}
        for g in groups
    ]


def _reconcile_milestones(llm_milestones: list[dict], groups: list[list[dict]]) -> list[dict]:
    """LLM 라벨을 묶음과 순서대로 합치되, 날짜는 git 에서 결정론적으로 덮어쓴다.

    LLM 이 JSON 을 깨뜨리거나 개수가 안 맞아도 묶음 수만큼 마일스톤을 항상 보장한다
    (라벨 빈 칸은 _seed_milestones 로 폴백). 날짜 환각을 원천 차단한다.
    """
    seeds = _seed_milestones(groups)
    out: list[dict] = []
    for i, group in enumerate(groups):
        desc = ""
        if i < len(llm_milestones):
            desc = (llm_milestones[i].get("description") or "").strip()
        out.append({"date": _group_date(group), "description": desc or seeds[i]["description"]})
    return out


async def prepare_summary(
    db: AsyncSession, repo_path: str, file_path: str, commits: list[dict]
) -> dict:
    """① 백본 upsert + ② 이력 조회 + ③ 캐시 조회까지 수행하고, 라우터가 응답 형식을
    결정할 수 있도록 결과를 분기해 반환한다.

    캐시 적중: {"cached": True, "result": {"summary","milestones"}}  → 라우터가 JSON 즉시 응답
    캐시 미스: {"cached": False, "ctx": {...}}                       → 라우터가 SSE 스트림 응답
    """
    logger.info("[timeline] ▶ prepare_summary 시작 — repo=%s  file=%s  ext_commits=%d건",
                repo_path, file_path, len(commits))

    # ② 백본 upsert + 파일 전체 이력 조회
    file = await crud.upsert_commits(db, repo_path, file_path, commits)
    logger.info("[timeline] file 확보 — file_id=%d  repo_id=%d", file.id, file.repo_id)

    stored = await crud.get_commits(db, file.id)
    logger.info("[timeline] DB 커밋 이력 — %d건", len(stored))
    if not stored:
        raise ValueError("저장된 커밋 이력이 없습니다.")

    # ③ 요약 캐시 조회
    set_hash = compute_commit_set_hash(stored)
    logger.info("[timeline] commit_set_hash=%s", set_hash[:16] + "…")

    cached = await crud.get_cached_summary(db, file.id, set_hash)
    if cached:
        logger.info("[timeline] ✅ 캐시 적중 — file_id=%d", file.id)
        return {"cached": True, "result": {"summary": cached.summary, "milestones": cached.milestones or []}}

    logger.info("[timeline] ❌ 캐시 미스 — 스트리밍 응답으로 전환")
    return {
        "cached": False,
        "ctx": {
            "file": file,
            "set_hash": set_hash,
            "repo_path": repo_path,
            "file_path": file_path,
            "stored": stored,
        },
    }


async def stream_summary(db: AsyncSession, ctx: dict) -> AsyncGenerator[str, None]:
    """캐시 미스 시 Bedrock 토큰을 SSE(`data: ...\\n\\n`) 프레임으로 실시간 전달한다.

    스트림이 끝나면(요구사항 3) 누적 텍스트를 파싱해 timeline_summaries 캐시에
    저장하는 DB 적재 로직을 그대로 수행한다 — 캐시 히트 로직과의 정합성 유지.
    """
    file       = ctx["file"]
    set_hash   = ctx["set_hash"]
    repo_path  = ctx["repo_path"]
    file_path  = ctx["file_path"]
    stored     = ctx["stored"]

    # 미스 → 전체 의미있는 이력을 마일스톤 묶음으로 그룹핑(결정론적) → Bedrock 입력 구성.
    #   '어떤 커밋을 묶을지'는 여기서 규칙으로 정하고, LLM 은 묶음 라벨만 생성한다.
    meaningful = filter_meaningful(stored)
    groups = group_commits_into_milestones(meaningful)
    groups_text = _format_groups_for_prompt(groups)
    logger.info("[timeline] 마일스톤 그룹핑 — 커밋 %d건 → 묶음 %d개  (입력 %d자)",
                len(meaningful), len(groups), len(groups_text))

    latest = stored[0] if stored else {}
    parsed = _parse_commit(latest.get("subject", ""))
    logger.info("[timeline] 🔴 스트리밍 시작 — file=%s  type=%s  domain=%s",
                file_path, parsed["type"], parsed["domain"])

    full_text = ""
    try:
        async for delta in stream_file_summary(
            file_path=file_path,
            groups_text=groups_text,
            num_groups=len(groups),
            commit_type=parsed["type"],
            commit_domain=parsed["domain"],
        ):
            full_text += delta
            yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
    except Exception as exc:
        logger.exception("[timeline] 스트리밍 중 오류 — file=%s : %s", file_path, exc)
        yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
        return

    # 스트림 종료 → 누적 텍스트 파싱 + 마일스톤 날짜를 git 기준으로 정합(환각 차단) + 캐시 저장.
    result = parse_ai_response(full_text)
    result["milestones"] = _reconcile_milestones(result.get("milestones", []), groups)
    logger.info("[timeline] Bedrock 스트리밍 완료 — summary=%d자  milestones=%d건",
                len(result.get("summary", "")), len(result.get("milestones", [])))

    await crud.save_summary(db, file.id, set_hash, result)
    logger.info("[timeline] 캐시 저장 완료 — file_id=%d  hash=%s", file.id, set_hash[:16] + "…")

    yield f"data: {json.dumps({'done': True, **result}, ensure_ascii=False)}\n\n"
