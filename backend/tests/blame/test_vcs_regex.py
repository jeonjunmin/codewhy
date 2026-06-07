"""_extract_issue_numbers / _extract_attachments 정규식 견고성."""

from app.core.vcs import _extract_attachments, _extract_issue_numbers


def test_extracts_closes_and_fixes_keywords():
    body = "Closes #12. Also fixes #34 and resolves GH-56."
    assert _extract_issue_numbers(body) == [12, 34, 56]


def test_extracts_bare_hash_references():
    # "feat[blame]: #2 …" 처럼 키워드 없이 #N 만 와도 추출
    body = "feat[blame]: 본문에 #99 참조"
    assert _extract_issue_numbers(body) == [99]


def test_dedup_preserves_order():
    body = "Closes #12, refs #5, again #12"
    assert _extract_issue_numbers(body) == [12, 5]


def test_no_matches_returns_empty():
    assert _extract_issue_numbers("아무 번호도 없는 본문") == []
    assert _extract_issue_numbers("") == []


def test_github_user_attachments_recognized():
    body = "스크린샷 https://github.com/user-attachments/files/123/spec.pdf 참조"
    attachments = _extract_attachments(body)
    assert len(attachments) == 1
    assert attachments[0].url.endswith("/spec.pdf")


def test_markdown_link_uses_label_as_attachment_name():
    body = "기획서: [결제 정책 v2](https://github.com/user-attachments/files/1/policy.pdf)"
    attachments = _extract_attachments(body)
    assert len(attachments) == 1
    assert attachments[0].label == "결제 정책 v2"


def test_pdf_url_outside_user_attachments_still_matched():
    body = "외부 위키: https://wiki.example.com/spec.pdf"
    attachments = _extract_attachments(body)
    assert any(a.url.endswith("/spec.pdf") for a in attachments)


def test_dedup_attachments_by_url():
    url = "https://github.com/user-attachments/files/1/a.pdf"
    body = f"[A]({url}) 그리고 직접 링크 {url}"
    attachments = _extract_attachments(body)
    assert len(attachments) == 1


def test_empty_body_returns_empty():
    assert _extract_attachments("") == []
