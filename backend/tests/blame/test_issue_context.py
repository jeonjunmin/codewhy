"""돋보기 컨텍스트에 이슈 활동/댓글·첨부 표기가 들어가는지 + 첨부 멀티모달 수집 검증.

모두 네트워크 없는 순수 함수. _collect_issue_attachment_blocks 는 다운로드 단계를
monkeypatch 로 가로채 'dedup 된 항목 리스트'만 검증한다(실제 HTTP 없음).
"""

from app.core.vcs import Attachment, Comment, Issue
from app.features.blame import service
from app.features.blame.service import (
    _MAX_ACTIVITY_ITEMS,
    _MAX_COMMENT_CHARS,
    _collect_issue_attachment_blocks,
    _format_issue_activity,
    _format_issues,
)


def _comment(**kw) -> Comment:
    return Comment(kind=kw.pop("kind", "comment"), **kw)


# ── _format_issue_activity ───────────────────────────────────────────────────

def test_activity_empty_returns_blank():
    assert _format_issue_activity([]) == ""


def test_activity_renders_human_comment():
    out = _format_issue_activity([_comment(author="hong", created_at="2026-06-01T09:00:00Z", body="결제 취소 정책 확정")])
    assert "hong" in out and "결제 취소 정책 확정" in out and "2026-06-01" in out


def test_activity_includes_commit_and_close_events_but_skips_labeled():
    comments = [
        _comment(kind="event", event="labeled", author="kim", label="bug"),
        _comment(kind="event", event="committed", commit_sha="abcdef1234", commit_summary="환율 반영", created_at="2026-06-02T00:00:00Z"),
        _comment(kind="event", event="closed", author="lee", created_at="2026-06-03T00:00:00Z"),
    ]
    out = _format_issue_activity(comments)
    assert "[committed] abcdef1" in out and "환율 반영" in out
    assert "[closed] lee" in out
    assert "labeled" not in out and "bug" not in out  # 부수 이벤트는 토큰 절약 위해 생략


def test_activity_truncates_long_comment():
    out = _format_issue_activity([_comment(author="a", body="x" * (_MAX_COMMENT_CHARS + 50))])
    assert out.endswith("…")
    assert len(out) < _MAX_COMMENT_CHARS + 60


def test_activity_keeps_only_latest_items():
    comments = [_comment(author=f"u{i}", body=f"c{i}") for i in range(_MAX_ACTIVITY_ITEMS + 5)]
    out = _format_issue_activity(comments)
    assert out.count("\n") + 1 == _MAX_ACTIVITY_ITEMS  # 끝(최신)에서 N개만
    assert "c0" not in out and f"c{_MAX_ACTIVITY_ITEMS + 4}" in out


# ── _format_issues ───────────────────────────────────────────────────────────

def _issue(**kw) -> Issue:
    kw.setdefault("title", "결제 취소")
    kw.setdefault("url", "https://example/1")
    return Issue(number=kw.pop("number", 12), **kw)


def test_issue_block_includes_meta_and_activity():
    issue = _issue(
        state="closed", labels=["payment", "urgent"], assignee="hong",
        body="해외 결제 수수료 누락 수정",
        comments=[_comment(author="kim", body="QA 통과")],
    )
    out = _format_issues([issue])
    assert "상태: closed" in out and "라벨: payment, urgent" in out and "담당자: hong" in out
    assert "활동/댓글:" in out and "QA 통과" in out


def test_issue_block_marks_skipped_attachment_only():
    issue = _issue(attachments=[
        Attachment(label="spec.pdf", url="https://x/spec.pdf"),
        Attachment(label="huge.hwp", url="https://x/huge.hwp"),
    ])
    out = _format_issues([issue], skipped_attachments=["huge.hwp"])
    # 미첨부 라벨에만 안내가 붙고, 정상 첨부엔 안 붙는다
    assert "huge.hwp ※ 내용 미첨부" in out
    spec_line = next(l for l in out.splitlines() if "spec.pdf" in l)
    assert "미첨부" not in spec_line


def test_no_issues_message_unchanged():
    assert "연관 이슈 없음" in _format_issues([])


# ── _collect_issue_attachment_blocks ─────────────────────────────────────────

def test_collect_dedupes_across_issues_and_comments(monkeypatch):
    captured = {}

    def fake_build(items):
        captured["items"] = items
        return [], []

    monkeypatch.setattr(service.issue_attachments, "build_blocks_from_list", fake_build)

    issues = [
        _issue(number=1, attachments=[Attachment(label="a", url="https://x/a.pdf")],
               comments=[_comment(attachments=[Attachment(label="b", url="https://x/b.png")])]),
        # 같은 url(a.pdf)을 다른 이슈가 또 참조 — dedup 되어야 한다
        _issue(number=2, attachments=[Attachment(label="a2", url="https://x/a.pdf")]),
    ]
    _collect_issue_attachment_blocks(issues)
    urls = [it["url"] for it in captured["items"]]
    assert urls == ["https://x/a.pdf", "https://x/b.png"]  # 순서 보존 + 중복 제거


def test_collect_skips_download_when_no_attachments(monkeypatch):
    called = {"n": 0}

    def fake_build(items):
        called["n"] += 1
        return [], []

    monkeypatch.setattr(service.issue_attachments, "build_blocks_from_list", fake_build)
    blocks, skipped = _collect_issue_attachment_blocks([_issue(number=3)])
    assert blocks == [] and skipped == []
    assert called["n"] == 0  # 첨부 0건이면 다운로드 인프라를 부르지 않는다
