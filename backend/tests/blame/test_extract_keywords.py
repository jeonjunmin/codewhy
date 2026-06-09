"""extract_keywords — 불용어/도메인 우선/중복 제거/순서 보존."""

from app.features.blame.service import extract_keywords


def test_strips_conventional_commit_prefix():
    # "feat[blame]: ..." 형태에서 prefix 자체는 키워드에 포함되지 않음
    result = extract_keywords("feat[blame]: 결제 취소 정책 변경")
    assert "feat" not in result
    assert "blame" not in result
    assert "결제" in result
    assert "취소" in result


def test_filters_stopwords():
    # conventional-commit 동사와 일반 한국어 동사는 모두 stopword
    result = extract_keywords("feat: 결제 수정 추가 변경")
    assert "수정" not in result
    assert "추가" not in result
    assert "변경" not in result
    assert "결제" in result


def test_domain_terms_pushed_to_front():
    # _DOMAIN_TERMS (결제/정산/...) 가 다른 토큰보다 앞에 와야 KB 검색 정확도↑
    result = extract_keywords("feat: 사용자 결제 화면 정산 로직 개편")
    # 결제·정산이 사용자·화면·로직 보다 앞
    assert result.index("결제") < result.index("사용자")
    assert result.index("정산") < result.index("로직")


def test_dedup_preserves_first_occurrence_order():
    # 같은 토큰이 반복되면 첫 등장 순서만 보존
    result = extract_keywords("feat: 결제 화면 결제 로직 화면 정리")
    assert result.count("결제") == 1
    assert result.count("화면") == 1
    assert result.index("결제") < result.index("화면")


def test_short_english_tokens_dropped():
    # 영숫자 토큰은 2자 이상만 — 한 글자 잡음 제거
    result = extract_keywords("fix: a b c API 호출 정리")
    assert "a" not in result
    assert "b" not in result
    assert "API" in result


def test_empty_message_returns_empty_list():
    assert extract_keywords("") == []
    assert extract_keywords("   ") == []
