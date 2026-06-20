"""_extract_issue_numbers / _extract_attachments 정규식 + 호스트 판별 견고성."""

from app.core.vcs import _extract_attachments, _extract_issue_numbers, _parse_remote_url


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


def test_image_url_recognized_as_attachment():
    # 외부 호스트의 raw 이미지도 첨부로 인식돼 프런트엔드 인라인 미리보기로 이어진다.
    body = "스크린샷 ![버그](https://img.example.com/shot.png) 첨부"
    attachments = _extract_attachments(body)
    assert any(a.url.endswith("/shot.png") for a in attachments)


def test_html_img_src_excludes_trailing_quote():
    # GitHub 가 붙이는 HTML <img src="..."> 의 닫는 따옴표가 URL 에 섞이면 안 된다.
    body = '<img width="348" src="https://github.com/user-attachments/assets/580cd28d-9551" />'
    attachments = _extract_attachments(body)
    assert len(attachments) == 1
    assert attachments[0].url.endswith("580cd28d-9551")
    assert '"' not in attachments[0].url


def test_dedup_attachments_by_url():
    url = "https://github.com/user-attachments/files/1/a.pdf"
    body = f"[A]({url}) 그리고 직접 링크 {url}"
    attachments = _extract_attachments(body)
    assert len(attachments) == 1


def test_empty_body_returns_empty():
    assert _extract_attachments("") == []


# ─── 호스트 판별 (SSRF·토큰 유출 방어) ──────────────────────────────────────

def test_github_https_and_ssh_parsed():
    for url in (
        "https://github.com/owner/repo.git",
        "git@github.com:owner/repo.git",
        "https://github.com/group/sub/repo",
    ):
        remote = _parse_remote_url(url)
        assert remote is not None and remote.host == "github"
        # base 는 항상 정규 API 호스트로 고정 — 도메인 문자열이 끼어들지 않는다.
        assert remote.base == "https://api.github.com"


def test_gitlab_com_parsed():
    remote = _parse_remote_url("https://gitlab.com/owner/repo.git")
    assert remote is not None and remote.host == "gitlab"
    assert remote.base == "https://gitlab.com/api/v4"


def test_lookalike_domain_rejected():
    # 'github'/'gitlab' 이 substring 으로 들어간 위장 도메인은 인정하지 않는다(토큰 유출 차단).
    for url in (
        "https://github.com.attacker.com/owner/repo.git",
        "https://gitlab.com.evil.example/owner/repo.git",
        "https://notgithub.com/owner/repo.git",
        "https://my-gitlab-mirror.io/owner/repo.git",
    ):
        assert _parse_remote_url(url) is None


def test_self_hosted_gitlab_requires_allowlist(monkeypatch):
    url = "https://git.example.com/owner/repo.git"
    # 화이트리스트 미설정이면 거부.
    monkeypatch.delenv("CODEWHY_GITLAB_HOSTS", raising=False)
    assert _parse_remote_url(url) is None
    # 화이트리스트에 등록하면 허용(대소문자 무시).
    monkeypatch.setenv("CODEWHY_GITLAB_HOSTS", "git.example.com")
    remote = _parse_remote_url("https://GIT.EXAMPLE.COM/owner/repo.git")
    assert remote is not None and remote.host == "gitlab"
    assert remote.base == "https://git.example.com/api/v4"
