"""Timeline Summary 비즈니스 로직.

git 커밋 이력을 가져와 LangGraph Map-Reduce 파이프라인으로 전달한다.
AI 호출, 분류, 파싱 등 세부 로직은 graph.py 에서 관리한다.

👤 담당: 개발자 B
"""

from app.core import git
from app.features.timeline.graph import run_timeline_graph


def summarize(repo_path: str, file_path: str) -> dict:
    commits = git.get_file_log(repo_path, file_path)
    if not commits:
        raise ValueError("커밋 이력이 없습니다.")

    return run_timeline_graph(repo_path, file_path, commits)
