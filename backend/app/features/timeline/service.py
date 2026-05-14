"""Timeline Summary 비즈니스 로직.

파일의 git 커밋 이력을 모아 Claude 에게 전체 요약과 마일스톤을 JSON 으로 부탁한다.

👤 담당: 개발자 B
"""

import json

from app.core import git
from app.core.ai_client import call_claude


def summarize(repo_path: str, file_path: str) -> dict:
    commits = git.get_file_log(repo_path, file_path)
    if not commits:
        raise ValueError("커밋 이력이 없습니다.")
    return _summarize_timeline(commits)


def _summarize_timeline(commits: list[dict]) -> dict:
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
    {{"date": "YYYY-MM-DD", "description": "주요 변경 내용 한 줄"}}
  ]
}}"""

    raw = call_claude(prompt, max_tokens=1000)
    return json.loads(raw)
