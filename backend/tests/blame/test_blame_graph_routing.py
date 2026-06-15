"""blame_graph 라우팅 — 노이즈 우회 / 의미있는 커밋 fan-out 검증.

비스트리밍 경로(run_blame_graph = ainvoke)로 그래프 분기를 확인한다.
외부 의존(git/PR/이슈/Bedrock)은 모두 monkeypatch 로 대체해 네트워크 없이 돈다.
"""

import asyncio

from langchain_core.messages import AIMessage

from app.ai import blame_graph as bg
from app.core import git
from app.features.blame import service as svc


def _info(message: str) -> git.BlameInfo:
    return git.BlameInfo(
        commit_hash="a" * 40, author="개발자A", date="2026-01-02",
        message=message, diff="@@ -1 +1 @@\n-old\n+new", added=1, removed=1,
    )


class _FakeLLM:
    """get_bedrock_llm 대체 — with_config 체이닝 후 ainvoke 로 고정 응답."""
    def with_config(self, *_, **__):
        return self

    async def ainvoke(self, _messages):
        return AIMessage(content="결제 취소 정책 변경 때문입니다.")


def _patch_common(monkeypatch):
    monkeypatch.setattr(svc, "_build_line_history", lambda *a, **k: [])
    monkeypatch.setattr(bg, "get_bedrock_llm", lambda *a, **k: _FakeLLM())


def test_noise_commit_skips_external_calls(monkeypatch):
    _patch_common(monkeypatch)

    # 노이즈 경로에서 PR/이슈/후속커밋 조회가 호출되면 테스트 실패
    def _boom(*a, **k):
        raise AssertionError("노이즈 커밋은 외부 조회를 하면 안 된다")

    monkeypatch.setattr(svc, "_safe_find_pr", _boom)
    monkeypatch.setattr(svc, "_safe_find_issues", _boom)
    monkeypatch.setattr(git, "find_followup_commits", _boom)

    result = asyncio.run(bg.run_blame_graph(
        "/repo", "x.py", 3,
        info=_info("docs: README 환경변수 보완"), branch="main", ticket=None,
    ))

    assert result["commitHash"] == "a" * 40
    assert result["explanation"]            # 노이즈 정형 문구가 채워짐
    assert result["relatedChanges"] == []   # 외부 조회 없음
    assert result["issueUrl"] is None
    assert result["aiSuggestion"] is None


def test_meaningful_commit_runs_full_pipeline(monkeypatch):
    _patch_common(monkeypatch)

    calls = {"pr": 0, "issues": 0, "followups": 0}

    def _fake_pr(*a, **k):
        calls["pr"] += 1
        return None

    def _fake_issues(*a, **k):
        calls["issues"] += 1
        return []

    def _fake_followups(*a, **k):
        calls["followups"] += 1
        return []

    monkeypatch.setattr(svc, "_safe_find_pr", _fake_pr)
    monkeypatch.setattr(svc, "_safe_find_issues", _fake_issues)
    monkeypatch.setattr(git, "find_followup_commits", _fake_followups)

    result = asyncio.run(bg.run_blame_graph(
        "/repo", "pay.py", 10,
        info=_info("feat[payment]: 결제 취소 추가"), branch="main", ticket="PAY-1",
    ))

    # 의미있는 커밋은 PR·이슈·후속커밋 노드를 모두 거친다
    assert calls == {"pr": 1, "issues": 1, "followups": 1}
    assert result["explanation"] == "결제 취소 정책 변경 때문입니다."
    assert result["aiDegraded"] is False
