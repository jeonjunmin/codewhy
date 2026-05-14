"""Context Blame 비즈니스 로직.

git 로 라인 단위 마지막 커밋을 가져와 Claude 에게 "왜 바꿨는지" 설명을 부탁한다.

👤 담당: 개발자 A
"""

from app.core import git
from app.core.ai_client import call_claude


def analyze_blame(repo_path: str, file_path: str, line: int) -> dict:
    info = git.get_blame_info(repo_path, file_path, line)
    explanation = _explain_blame(info)
    return {
        "explanation": explanation,
        "commitHash": info.commit_hash,
        "author": info.author,
        "date": info.date,
    }


def _explain_blame(info: git.BlameInfo) -> str:
    prompt = f"""다음 git 커밋 정보를 바탕으로, 개발자가 이 코드를 왜 변경했는지 한국어로 1~2문장으로 설명해주세요.
비개발자도 이해할 수 있게 쉽게 작성하세요.

작성자: {info.author}
날짜: {info.date}
커밋 메시지: {info.message}
변경 내용:
{info.diff}"""

    return call_claude(prompt, max_tokens=300)
