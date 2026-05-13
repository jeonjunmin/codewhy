import os
import anthropic

_client = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def explain_blame(author: str, date: str, commit_message: str, diff: str) -> str:
    """커밋 정보를 바탕으로 변경 이유를 한국어로 설명한다."""
    prompt = f"""다음 git 커밋 정보를 바탕으로, 개발자가 이 코드를 왜 변경했는지 한국어로 1~2문장으로 설명해주세요.
비개발자도 이해할 수 있게 쉽게 작성하세요.

작성자: {author}
날짜: {date}
커밋 메시지: {commit_message}
변경 내용:
{diff}"""

    message = get_client().messages.create(
        model="claude-opus-4-7",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def summarize_timeline(commits: list[dict]) -> dict:
    """커밋 이력을 전체 요약과 주요 마일스톤으로 정리한다."""
    commit_text = "\n".join(
        f"- [{c['date']}] {c['author']}: {c['subject']}" for c in commits[:50]
    )
    prompt = f"""다음은 한 파일의 git 커밋 이력입니다. 아래 JSON 형식으로 응답해주세요.

커밋 이력:
{commit_text}

응답 형식 (JSON만 출력):
{{
  "summary": "파일 전체 역사를 2~3문장으로 요약",
  "milestones": [
    {{"date": "YYYY-MM-DD", "description": "주요 변경 내용 한 줄"}},
    ...
  ]
}}"""

    message = get_client().messages.create(
        model="claude-opus-4-7",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    import json
    return json.loads(message.content[0].text)
