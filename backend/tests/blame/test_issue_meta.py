"""이슈 메타 변환 — 담당자(assignee) 폴백 견고성.

_issue_from_github/_gitlab 가 단일 assignee 가 비어도 assignees 배열에서
첫 담당자를 골라내는지 검증한다(네트워크 없이 순수 변환).
"""

from app.core.vcs import _issue_from_github, _issue_from_gitlab, _pick_login


def test_pick_login_prefers_single_field():
    assert _pick_login({"login": "hong"}, [{"login": "kim"}], "login") == "hong"


def test_pick_login_falls_back_to_array_when_single_empty():
    assert _pick_login(None, [{"login": "kim"}], "login") == "kim"
    assert _pick_login({}, [{"login": "kim"}], "login") == "kim"


def test_pick_login_empty_when_no_assignee():
    assert _pick_login(None, [], "login") == ""
    assert _pick_login(None, None, "login") == ""


def test_github_issue_reads_single_assignee():
    issue = _issue_from_github({"title": "T", "assignee": {"login": "hong"}}, 1)
    assert issue.assignee == "hong"


def test_github_issue_falls_back_to_assignees_array():
    # assignee 가 null 이고 assignees 만 채워진 케이스(멀티 지정 등)
    issue = _issue_from_github({"title": "T", "assignee": None, "assignees": [{"login": "hong"}]}, 1)
    assert issue.assignee == "hong"


def test_gitlab_issue_uses_username_key():
    issue = _issue_from_gitlab({"title": "T", "assignees": [{"username": "hong"}]}, 1)
    assert issue.assignee == "hong"
