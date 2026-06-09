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
    commits_text: str,
    commit_type: str,
    commit_domain: str,
) -> str:
    perspective = _TYPE_PERSPECTIVE.get(commit_type, f"'{commit_type}' 작업의 역사")
    domain_hint = f"[{commit_domain}] 도메인 " if commit_domain else ""

    return f"""아래는 {domain_hint}소스 파일 `{file_path}` 의 Git 커밋 이력입니다.
이 파일은 {commit_domain or '알 수 없는'} 도메인의 '{commit_type}' 작업이야.
**{perspective}** 관점으로 변경 흐름을 분석하세요.

반드시 아래 JSON 형식으로만 응답하세요. 코드블록, 마크다운, 추가 설명 없이 JSON 만 반환하세요:
{{
  "summary": "이 파일의 변경 흐름 요약 (한국어 3~5문장, 기획/비즈니스 의도 중심)",
  "milestones": [
    {{"date": "YYYY-MM-DD", "description": "주요 변경 내용 한 줄"}},
    {{"date": "YYYY-MM-DD", "description": "주요 변경 내용 한 줄"}}
  ]
}}

milestones 는 커밋 이력에서 중요한 변경점 2~5개를 날짜순으로 추출하세요.

[커밋 이력]
{commits_text}"""


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
    commits_text: str,
    commit_type: str,
    commit_domain: str,
) -> AsyncGenerator[str, None]:
    """Bedrock 응답을 토큰(델타) 단위로 즉시 yield 한다.

    SSE 프레이밍(`data: ...\\n\\n`)이나 누적/파싱은 호출 측(service.py) 책임이다 —
    이 함수는 raw text delta 만 흘려보낸다.
    """
    llm = get_bedrock_llm(max_tokens=800)
    prompt = _build_prompt(
        file_path=file_path,
        commits_text=commits_text,
        commit_type=commit_type,
        commit_domain=commit_domain,
    )
    async for chunk in llm.astream([HumanMessage(content=prompt)]):
        piece = chunk.content
        if piece:
            yield piece
