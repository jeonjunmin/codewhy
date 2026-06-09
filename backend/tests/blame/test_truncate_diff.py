"""_truncate_diff — hunk 헤더 우선 보존 전략."""

from app.features.blame.service import _MAX_DIFF_CHARS, _truncate_diff


def _make_hunk(header: str, body_lines: int, marker: str = "+") -> str:
    body = "\n".join(f"{marker} line-{marker}-{i}" for i in range(body_lines))
    return f"{header}\n{body}"


def test_short_diff_returned_as_is():
    diff = "@@ -1,1 +1,1 @@\n+a"
    assert _truncate_diff(diff) == diff


def test_keeps_first_hunks_when_budget_allows():
    # 작은 hunk 두 개면 통째로 보존
    diff = "\n".join([
        "diff --git a/x b/x",
        "--- a/x",
        "+++ b/x",
        _make_hunk("@@ -1,3 +1,3 @@", 3),
        _make_hunk("@@ -10,3 +10,3 @@", 3),
    ])
    out = _truncate_diff(diff)
    # 짧으니까 바뀐 게 없어야 함
    assert "@@ -1,3 +1,3 @@" in out
    assert "@@ -10,3 +10,3 @@" in out


def test_skipped_hunks_emit_header_only_block():
    # _MAX_DIFF_CHARS 를 초과하도록 큰 hunk 여러 개 만든다
    big_body = 1500  # 한 hunk 당 ~25KB 정도 → 두 번째부터는 잘림
    diff = "\n".join([
        "diff --git a/big b/big",
        "--- a/big",
        "+++ b/big",
        _make_hunk("@@ -1,10 +1,10 @@", big_body),
        _make_hunk("@@ -2000,10 +2000,10 @@", big_body),
        _make_hunk("@@ -4000,10 +4000,10 @@", big_body),
    ])
    assert len(diff) > _MAX_DIFF_CHARS
    out = _truncate_diff(diff)
    # 결과는 상한 근처여야 함 (정확히 같진 않지만 두 배는 절대 안 됨)
    assert len(out) < _MAX_DIFF_CHARS * 1.2
    # 잘린 hunks 블록과 그 헤더가 보존되어야 함
    assert "[잘린 hunks" in out
    assert "@@ -2000,10 +2000,10 @@" in out or "@@ -4000,10 +4000,10 @@" in out


def test_non_patch_diff_falls_back_to_head_tail():
    # @@ 헤더가 없으면 head+tail 폴백 — 중략 마커가 들어가야 함
    diff = "A" * (_MAX_DIFF_CHARS + 200)
    out = _truncate_diff(diff)
    assert "중략" in out
    assert out.startswith("A")
    assert out.endswith("A")
