"""_github_timeline_entry — Timeline payload → Comment 변환 견고성.

네트워크 없이 순수 변환만 검증한다(실제 페치는 _github_issue_timeline 가 담당).
"""

from app.core.vcs import _github_timeline_entry


def test_commented_event_becomes_comment_with_attachments():
    c = _github_timeline_entry({
        "event": "commented",
        "user": {"login": "kimpm"},
        "created_at": "2026-03-12T00:00:00Z",
        "body": "검토 완료 — 자료: [정책](https://github.com/user-attachments/files/1/p.pdf)",
    })
    assert c is not None
    assert c.kind == "comment"
    assert c.author == "kimpm"
    assert c.body.startswith("검토 완료")
    assert len(c.attachments) == 1


def test_labeled_event_carries_label_and_actor():
    c = _github_timeline_entry({
        "event": "labeled",
        "actor": {"login": "kimpm"},
        "label": {"name": "spec"},
        "created_at": "2026-03-11T00:00:00Z",
    })
    assert c.kind == "event"
    assert c.event == "labeled"
    assert c.author == "kimpm"
    assert c.label == "spec"


def test_assigned_event_carries_assignee():
    c = _github_timeline_entry({
        "event": "assigned",
        "actor": {"login": "kimpm"},
        "assignee": {"login": "hong"},
        "created_at": "2026-03-11T00:00:00Z",
    })
    assert c.event == "assigned"
    assert c.assignee == "hong"


def test_committed_event_uses_message_first_line():
    c = _github_timeline_entry({
        "event": "committed",
        "sha": "a3f9c1d",
        "message": "PaymentService.kt L4-L10에 수수료 로직 반영\n\n상세 본문",
        "author": {"name": "홍길동", "date": "2026-03-15T00:00:00Z"},
    })
    assert c.event == "committed"
    assert c.author == "홍길동"
    assert c.commit_sha == "a3f9c1d"
    assert c.commit_summary == "PaymentService.kt L4-L10에 수수료 로직 반영"


def test_referenced_event_keeps_commit_id_without_message():
    c = _github_timeline_entry({
        "event": "referenced",
        "actor": {"login": "hong"},
        "commit_id": "deadbeef",
        "created_at": "2026-03-15T00:00:00Z",
    })
    assert c.event == "referenced"
    assert c.commit_sha == "deadbeef"
    assert c.commit_summary == ""


def test_uninteresting_event_is_dropped():
    assert _github_timeline_entry({"event": "subscribed", "actor": {"login": "x"}}) is None
    assert _github_timeline_entry({"event": "mentioned"}) is None
