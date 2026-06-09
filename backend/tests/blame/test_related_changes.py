"""_build_related_changes — 분류 우선순위·캡·"외 N건" 표기 검증."""

from app.core.vcs import Attachment, ChangedFile, Issue, PullRequest
from app.features.blame.service import _build_related_changes


def _issue(number=1, title="제목", attachments=None):
    return Issue(number=number, title=title, url="", body="", attachments=attachments or [])


def _file(path, status="modified", added=10):
    return ChangedFile(path=path, status=status, added=added)


def _pr(files):
    return PullRequest(url="", number=1, title="", body="", added=0, removed=0, files=files)


def test_attachments_become_doc_cards():
    issue = _issue(attachments=[Attachment(label="요구사항.pdf", url="https://x/p.pdf")])
    related = _build_related_changes([issue], None, [], "x.py")
    assert any(r["kind"] == "doc" and "요구사항.pdf" in r["title"] for r in related)


def test_issue_without_attachments_falls_back_to_issue_card():
    issue = _issue(number=12, title="결제 취소 정책")
    related = _build_related_changes([issue], None, [], "x.py")
    assert any(r["kind"] == "doc" and "Issue #12" in r["title"] for r in related)


def test_current_file_excluded_from_pr_section():
    pr = _pr([_file("x.py"), _file("y.py")])
    related = _build_related_changes([], pr, [], "x.py")
    titles = [r["title"] for r in related]
    assert not any("x.py" in t for t in titles)
    assert any("y.py" in t for t in titles)


def test_new_file_is_classified_as_branch():
    pr = _pr([_file("new.py", status="added", added=42)])
    related = _build_related_changes([], pr, [], "x.py")
    target = next(r for r in related if "new.py" in r["title"])
    assert target["kind"] == "branch"
    assert "신규 생성" in target["title"]


def test_pr_cap_emits_remaining_count():
    # PR 파일이 캡(5)보다 많으면 "외 N개 파일" 한 줄이 추가되어야 한다
    pr = _pr([_file(f"f{i}.py") for i in range(10)])
    related = _build_related_changes([], pr, [], "current.py")
    remainder = [r for r in related if r["title"].startswith("외 ")]
    assert len(remainder) == 1
    # 10 파일 - 5 노출 = 5 잔여
    assert "5" in remainder[0]["title"]


def test_security_term_classifies_followup_as_security():
    followups = [{"hash": "abc", "author": "A", "date": "2026-01-01", "subject": "KYC 감사 로그 추가"}]
    related = _build_related_changes([], None, followups, "x.py")
    sec = next(r for r in related if "KYC" in r["title"])
    assert sec["kind"] == "security"
