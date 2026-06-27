"""_strip_markdown — 평문 카드용 마크다운 제거(표 안전망 포함).

돋보기 콜아웃은 평문만 렌더하므로, 모델이 지시를 어기고 표/머리말/굵게를 내도
raw 흔적이 보이지 않게 정리되는지 검증한다(네트워크 없는 순수 함수).
"""

from app.features.blame.service import _strip_markdown


def test_plain_prose_unchanged():
    t = "해외 결제 수수료 누락을 막으려고 추가했습니다. 연관 이슈 #12 의 정책 변경을 반영했습니다."
    assert _strip_markdown(t) == t


def test_strips_bold_and_heading():
    assert _strip_markdown("## 제목\n**핵심** 내용") == "제목\n핵심 내용"


def test_flattens_table_rows_and_drops_separator():
    md = "| 상황 | 이전 | 이후 |\n|------|------|------|\n| 단순 커밋 | 분석 | 즉시 |"
    out = _strip_markdown(md)
    assert "|" not in out                       # raw 파이프가 남지 않는다
    assert "상황, 이전, 이후" in out
    assert "단순 커밋, 분석, 즉시" in out
    assert "----" not in out                     # 구분선 줄 제거


def test_prose_with_single_inner_pipe_untouched():
    # 시작·끝이 '|' 가 아닌 평문은 표 행으로 오인하지 않는다
    t = "옵션 A | 옵션 B 중 A 를 골랐습니다."
    assert _strip_markdown(t) == t


def test_empty_is_safe():
    assert _strip_markdown("") == ""
    assert _strip_markdown(None) == ""
