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
    {{"date": "YYYY-MM-DD", "description": "타이틀 한 줄\\n내용 한 줄"}}
  ]
}}

규칙:
- summary 형식 — 첫 줄에 '제목', 줄바꿈 문자(\\n) 뒤에 '상세 2문장' 을 쓰세요(제목과 상세를 \\n 으로 분리):
  · 첫 줄(제목) = 변경 흐름 전체를 압축한 간결한 명사구(20자 안팎). 쉼표로 문장을 이어 붙이지 말고, 마침표 없이 한 토막으로.
  · 줄바꿈 뒤(상세) = 그 변화의 배경·기획/비즈니스 의도를 풀어 쓴 2문장. 3문장을 넘기지 마세요.
  · 예: "블레임 스키마의 점진적 확장\\n초기엔 단순 정의로 시작했습니다. 이후 PR·이슈 맥락을 통합해 추적성을 강화했습니다."
- milestones 는 아래 '묶음'과 1:1로, 같은 순서로 정확히 {num_groups}개 만드세요.
- 각 description 은 '타이틀 한 줄' + 줄바꿈 문자(\\n) + '내용 한두 줄' 형식으로 쓰세요(타이틀과 내용을 \\n 으로 분리):
  · 타이틀 = 그 묶음이 이룬 변화를 압축한 명사구(15자 안팎, 쉼표·마침표 없이 한 토막).
  · 내용 = 그 변화의 의도/효과를 풀어 쓴 한두 줄(개별 커밋 나열·기술 용어가 아니라 기획·비즈니스 의도 중심). 두 문장을 넘기지 마세요.
  · 예: "이슈 맥락 연동\\n커밋과 GitHub 이슈를 이어 변경 사유를 한눈에 추적하게 했습니다."
- date 는 각 묶음에 표기된 마지막 날짜를 그대로 쓰세요. 임의의 날짜를 만들지 마세요.

[묶음별 커밋 이력]
{groups_text}"""


def parse_ai_response(raw: str) -> dict[str, Any]:
    """Bedrock 누적 응답 문자열에서 summary + milestones 를 추출한다.

    JSON 파싱 성공 시 그대로 반환, 실패 시 raw 텍스트를 summary 로 폴백한다.
    스트리밍 종료 후 누적된 전체 텍스트를 파싱할 때도 동일하게 사용한다.
    """
    # 코드블록 마커 제거 (```json ... ``` 형태 대응)
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()

    try:
        data = json.loads(cleaned)
        summary    = str(data.get("summary", "")).strip() or raw
        milestones = data.get("milestones", [])
        # milestones 각 항목을 {"date": str, "description": str} 형식으로 정제
        if isinstance(milestones, list):
            milestones = [
                {
                    "date":        str(m.get("date", "")),
                    "description": str(m.get("description", "")),
                }
                for m in milestones
                if isinstance(m, dict)
            ]
        else:
            milestones = []
        return {"summary": summary, "milestones": milestones}
    except (json.JSONDecodeError, Exception):
        return {"summary": raw, "milestones": []}


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
    llm = get_bedrock_llm(max_tokens=800)
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
