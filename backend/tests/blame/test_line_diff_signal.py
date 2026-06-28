"""_format_line_diff_signal — 라인 타이틀 grounding 신호 한 토막.

카운트(+N/-M), 함수명, 그리고 바뀐 라인 실제 텍스트(코드: old → new)를 렌더한다.
특히 '코드:' 는 메시지와 실제 변경이 어긋날 때(예: 메시지는 '문구 수정'인데
코드는 수수료율 변경) 타이틀을 코드 기준으로 교정하게 하는 핵심 신호다.
"""

from app.features.blame.service import _format_line_diff_signal


def test_empty_when_no_signal():
    assert _format_line_diff_signal({}) == ""


def test_counts_only():
    out = _format_line_diff_signal({"linesAdded": 1, "linesRemoved": 1})
    assert out == "  ⟪실제변경 +1/-1줄⟫"


def test_includes_changed_lines_text():
    out = _format_line_diff_signal({
        "linesAdded": 1,
        "linesRemoved": 1,
        "changedLines": "round(amount * 0.0) → round(amount * (0.03 if overseas else 0.0))",
    })
    assert "코드: round(amount * 0.0) → round(amount * (0.03 if overseas else 0.0))" in out
    assert out.startswith("  ⟪실제변경 +1/-1줄 · ")


def test_all_parts_in_order():
    out = _format_line_diff_signal({
        "linesAdded": 2,
        "linesRemoved": 0,
        "changedSymbols": "def calculate_fee",
        "changedLines": "+ return 0",
    })
    # 카운트 · 함수 · 코드 순서로 결합된다.
    assert out == "  ⟪실제변경 +2/-0줄 · 함수: def calculate_fee · 코드: + return 0⟫"


def test_blank_changed_lines_omitted():
    out = _format_line_diff_signal({"linesAdded": 1, "linesRemoved": 0, "changedLines": "   "})
    assert "코드:" not in out
