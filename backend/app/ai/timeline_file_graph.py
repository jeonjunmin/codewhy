"""파일별 타임라인 분석 — Bedrock 스트리밍 호출.

ChatGPT 류 실시간 출력 요구사항에 맞춰 LangGraph 단일 노드(ainvoke) 대신
async generator 로 직접 Bedrock 을 호출한다 — 토큰이 생성되는 즉시 yield 한다.
"""

import json
import re
from typing import Any, AsyncGenerator

from langchain_core.messages import HumanMessage

from app.core.bedrock import get_bedrock_llm

# ── type → 관점 레이블 매핑 ──────────────────────────────────────────────────

_TYPE_PERSPECTIVE: dict[str, str] = {
    "feat":     "기능 추가의 역사",
    "fix":      "디버깅 및 안정화의 역사",
    "refactor": "구조 개선의 역사",
    "perf":     "성능 최적화의 역사",
    "docs":     "문서화의 역사",
    "style":    "코드 스타일 정리의 역사",
}


def _build_prompt(
    file_path: str,
    groups_text: str,
    num_groups: int,
    commit_type: str,
    commit_domain: str,
) -> str:
    perspective = _TYPE_PERSPECTIVE.get(commit_type, f"'{commit_type}' 작업의 역사")
    domain_hint = f"[{commit_domain}] 도메인 " if commit_domain else ""

    return f"""아래는 {domain_hint}소스 파일 `{file_path}` 의 Git 커밋 이력을,
관련된 변경끼리 '묶음'으로 미리 그룹핑한 것입니다(같은 이슈/같은 작업/같은 시기 기준).
이 파일은 {commit_domain or '알 수 없는'} 도메인의 '{commit_type}' 작업이야.
**{perspective}** 관점으로 변경 흐름을 분석하세요.

반드시 아래 JSON 형식으로만 응답하세요. 코드블록, 마크다운, 추가 설명 없이 JSON 만 반환하세요:
{{
  "summary": "제목 한 줄\\n상세 2문장 (아래 'summary 형식' 규칙 참고)",
  "milestones": [
    {{"date": "YYYY-MM-DD", "description": "타이틀 한 줄\\n내용 한 줄", "major": true}}
  ]
}}

규칙:
- summary 형식 — 첫 줄에 '제목', 줄바꿈 문자(\\n) 뒤에 '상세 2문장' 을 쓰세요(제목과 상세를 \\n 으로 분리):
  · 첫 줄(제목) = 변경 흐름 전체를 압축한, 그 자체로 완결된 명사구(20자 안팎). 쉼표로 문장을 잇지 말고, 마침표 없이 한 토막으로.
    반드시 명사(체언)로 끝맺으세요 — '~까지/~부터/~으로/~의/~를' 같은 조사·연결어미로 끝내면 잘린 느낌을 주니 금지합니다.
    (✗ "초기 구축부터 고도화까지"  →  ✓ "초기 구축에서 고도화로 이어진 발전 과정"  또는  ✓ "점진적 고도화 과정")
  · 줄바꿈 뒤(상세) = 그 변화의 배경·기획/비즈니스 의도를 풀어 쓴 2문장. 3문장을 넘기지 마세요.
  · 예: "블레임 스키마의 점진적 확장\\n초기엔 단순 정의로 시작했습니다. 이후 PR·이슈 맥락을 통합해 추적성을 강화했습니다."
- milestones 는 아래 '묶음'과 1:1로, 같은 순서로 정확히 {num_groups}개 만드세요.
- 각 description 은 '타이틀 한 줄' + 줄바꿈 문자(\\n) + '내용 한두 줄' 형식으로 쓰세요(타이틀과 내용을 \\n 으로 분리):
  · 타이틀 = 그 묶음이 이룬 변화를 압축한 명사구(15자 안팎, 쉼표·마침표 없이 한 토막).
    제목과 마찬가지로 명사(체언)로 끝맺으세요 — '~까지/~부터/~으로/~의' 같은 조사·연결어미로 끝내지 마세요.
  · 내용 = 그 변화의 의도/효과를 풀어 쓴 한두 줄(개별 커밋 나열·기술 용어가 아니라 기획·비즈니스 의도 중심). 두 문장을 넘기지 마세요.
  · 예: "이슈 맥락 연동\\n커밋과 GitHub 이슈를 이어 변경 사유를 한눈에 추적하게 했습니다."
- 각 milestone 에 major(boolean) 를 넣으세요 — '주요 변곡점'이면 true, 평범한 일반 변경이면 false:
  · 주요 변곡점 = 파일 최초 생성, 핵심 기능 도입, 아키텍처/책임 구조 전환처럼 흐름을 바꾼 결정적 지점.
  · 전체 중 1~3개만 보수적으로 true (전부 true 금지). 최소 1개(보통 최초 생성)는 true 로 두세요.
- date 는 각 묶음에 표기된 마지막 날짜를 그대로 쓰세요. 임의의 날짜를 만들지 마세요.

[묶음별 커밋 이력]
{groups_text}"""


def _decode_json_string(inner: str) -> str:
    """JSON 문자열 '값 내부(따옴표 제외)' 한 토막의 이스케이프를 복원한다.

    json.loads 에 통째로 맡겨 \\n·\\"·\\\\·\\t·\\uXXXX 를 자동 변환하되,
    스트림이 이스케이프 도중 잘린 꼬리(\\ 또는 \\uA…)는 떼어내 파싱 실패를 막는다.
    """
    # 매달린 미완성 백슬래시(홀수 개) 제거
    m = re.search(r"(\\+)$", inner)
    if m and len(m.group(1)) % 2 == 1:
        inner = inner[:-1]
    inner = re.sub(r"\\u[0-9a-fA-F]{0,3}$", "", inner)   # 꼬리 잘린 \uXXXX 방어
    try:
        return json.loads('"' + inner + '"')
    except Exception:
        return inner


def _salvage_summary(raw: str) -> str:
    """깨지거나 잘린 JSON 에서 summary 문자열 값만 건져 이스케이프를 복원한다."""
    key = raw.find('"summary"')
    if key < 0:
        return ""
    open_q = raw.find('"', key + len('"summary"'))
    if open_q < 0:
        return ""
    # 값 닫는 따옴표 찾기 — 이스케이프된 따옴표(\")는 건너뛴다.
    i, end, n = open_q + 1, -1, len(raw)
    while i < n:
        if raw[i] == "\\":
            i += 2
            continue
        if raw[i] == '"':
            end = i
            break
        i += 1
    inner = raw[open_q + 1 : end] if end >= 0 else raw[open_q + 1 :]
    return _decode_json_string(inner)


def _salvage_milestones(raw: str) -> list[dict[str, Any]]:
    """깨진 JSON 의 milestones 배열에서 '완성된 객체'만 순서대로 건진다.

    날짜는 service._reconcile_milestones 가 git 기준으로 덮어쓰므로 비워 둔다
    (description/major 만 살리면 라벨 품질이 raw 폴백보다 크게 낫다)."""
    at = raw.find('"milestones"')
    if at < 0:
        return []
    out: list[dict[str, Any]] = []
    for obj in re.finditer(r"\{[^{}]*\}", raw[at:]):  # 잘린 마지막 객체는 자동 제외
        body = obj.group(0)
        dm = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"', body)
        if not dm:
            continue
        jm = re.search(r'"major"\s*:\s*(true|false)', body)
        out.append({
            "date": "",
            "description": _decode_json_string(dm.group(1)),
            "major": bool(jm and jm.group(1) == "true"),
        })
    return out


def parse_ai_response(raw: str) -> dict[str, Any]:
    """Bedrock 누적 응답 문자열에서 summary + milestones 를 추출한다.

    JSON 파싱 성공 시 그대로 반환, 실패(미완성/잘림 포함) 시 raw 를 통째로 쓰지 않고
    summary·milestones 값만 정규식으로 건져 낸다 — JSON 골격이 화면에 새지 않게 한다.
    스트리밍 종료 후 누적된 전체 텍스트를 파싱할 때도 동일하게 사용한다.
    """
    # 코드블록 마커 제거 (```json ... ``` 형태 대응)
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()

    try:
        data = json.loads(cleaned)
        summary    = str(data.get("summary", "")).strip() or _salvage_summary(cleaned)
        milestones = data.get("milestones", [])
        # milestones 각 항목을 {"date": str, "description": str} 형식으로 정제
        if isinstance(milestones, list):
            milestones = [
                {
                    "date":        str(m.get("date", "")),
                    "description": str(m.get("description", "")),
                    "major":       bool(m.get("major", False)),
                }
                for m in milestones
                if isinstance(m, dict)
            ]
        else:
            milestones = []
        return {"summary": summary, "milestones": milestones}
    except (json.JSONDecodeError, Exception):
        # 파싱 실패(주로 토큰 잘림으로 JSON 미완성) → 값만 건져 낸다.
        return {"summary": _salvage_summary(cleaned), "milestones": _salvage_milestones(cleaned)}


async def stream_file_summary(
    file_path: str,
    groups_text: str,
    num_groups: int,
    commit_type: str,
    commit_domain: str,
) -> AsyncGenerator[str, None]:
    """Bedrock 응답을 토큰(델타) 단위로 즉시 yield 한다.

    SSE 프레이밍(`data: ...\\n\\n`)이나 누적/파싱은 호출 측(service.py) 책임이다 —
    이 함수는 raw text delta 만 흘려보낸다.

    groups_text/num_groups 는 service.py 가 결정론적으로 묶은 마일스톤 그룹이다.
    LLM 은 '어떤 커밋을 묶을지'가 아니라 '각 묶음을 뭐라 부를지(라벨)'만 생성한다.
    """
    # 마일스톤 묶음이 많은 장수명 파일은 summary+milestones JSON 이 800 토큰을 넘겨
    # 중간에 잘리면(JSON 미완성) 파싱이 실패한다 → 넉넉히 잡아 truncation 을 막는다.
    llm = get_bedrock_llm(max_tokens=2048)
    prompt = _build_prompt(
        file_path=file_path,
        groups_text=groups_text,
        num_groups=num_groups,
        commit_type=commit_type,
        commit_domain=commit_domain,
    )
    async for chunk in llm.astream([HumanMessage(content=prompt)]):
        piece = chunk.content
        if piece:
            yield piece
