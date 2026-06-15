"""trace_graph — issue → ticket → semantic 폴백 체인 검증.

각 단계가 성공하면 이후 단계를 건드리지 않고, 모두 실패하면 빈 결과를 반환하는지 확인한다.
모든 외부 의존(git/GitHub)은 monkeypatch 로 대체한다.
"""

import asyncio

from app.ai import trace_graph as tg
from app.core import git, vcs


def _info(message="feat[payment]: 결제 취소 추가") -> git.BlameInfo:
    return git.BlameInfo(
        commit_hash="c" * 40, author="개발자A", date="2026-01-02",
        message=message, diff="", added=1, removed=1,
    )


def _issue(number=12, title="결제 취소 정책", body="본문"):
    return vcs.Issue(number=number, title=title, url=f"https://h/i/{number}", body=body, attachments=[])


def _pr(body="Closes #12"):
    return vcs.PullRequest(url="https://h/pr/1", number=1, title="", body=body, added=0, removed=0, files=[])


def _patch_base(monkeypatch):
    monkeypatch.setattr(tg.git, "get_blame_info", lambda *a, **k: _info())
    monkeypatch.setattr(tg.git, "get_current_branch", lambda *a, **k: "")
    monkeypatch.setattr(tg.vcs, "detect_remote", lambda *a, **k: object())  # truthy


def test_issue_match_short_circuits(monkeypatch):
    _patch_base(monkeypatch)
    search_calls = {"n": 0}

    monkeypatch.setattr(tg.vcs, "find_pr_for_commit", lambda *a, **k: _pr())
    monkeypatch.setattr(tg.vcs, "find_issues_from_pr_body", lambda *a, **k: [_issue()])
    monkeypatch.setattr(tg.vcs, "search_github_issues",
                        lambda *a, **k: search_calls.__setitem__("n", search_calls["n"] + 1) or [])

    results = asyncio.run(tg.atrace("/repo", "pay.py", 10))

    assert search_calls["n"] == 0          # ticket/semantic 단계 미진입
    assert len(results) == 1
    assert results[0]["matchType"] == "issue"
    assert results[0]["confidence"] is None  # issue = 확정


def test_falls_back_to_ticket(monkeypatch):
    _patch_base(monkeypatch)
    monkeypatch.setattr(tg.vcs, "find_pr_for_commit", lambda *a, **k: _pr(body=""))
    monkeypatch.setattr(tg.vcs, "find_issues_from_pr_body", lambda *a, **k: [])
    monkeypatch.setattr(tg, "extract_ticket", lambda *a, **k: "PAY-1")

    semantic_called = {"n": 0}

    def _search(remote, query, per_page=5):
        # ticket 검색(첫 호출)은 결과 있음, semantic 은 호출되지 않아야 함
        if query == "PAY-1":
            return [_issue()]
        semantic_called["n"] += 1
        return []

    monkeypatch.setattr(tg.vcs, "search_github_issues", _search)

    results = asyncio.run(tg.atrace("/repo", "pay.py", 10))

    assert semantic_called["n"] == 0
    assert results and results[0]["matchType"] == "ticket"
    assert results[0]["confidence"] == 0.8


def test_all_empty_returns_no_results(monkeypatch):
    _patch_base(monkeypatch)
    monkeypatch.setattr(tg.vcs, "find_pr_for_commit", lambda *a, **k: None)
    monkeypatch.setattr(tg.vcs, "find_issues_from_pr_body", lambda *a, **k: [])
    monkeypatch.setattr(tg, "extract_ticket", lambda *a, **k: None)
    monkeypatch.setattr(tg, "extract_keywords", lambda *a, **k: ["결제"])
    monkeypatch.setattr(tg.vcs, "search_github_issues", lambda *a, **k: [])

    results = asyncio.run(tg.atrace("/repo", "pay.py", 10))
    assert results == []


def test_unresolvable_commit_returns_empty(monkeypatch):
    # git blame 실패 → 폴백 체인 진입 없이 빈 결과
    def _boom(*a, **k):
        raise RuntimeError("not a git repo")

    monkeypatch.setattr(tg.git, "get_blame_info", _boom)
    monkeypatch.setattr(tg.vcs, "find_pr_for_commit",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("진입 금지")))

    results = asyncio.run(tg.atrace("/repo", "pay.py", 10))
    assert results == []
