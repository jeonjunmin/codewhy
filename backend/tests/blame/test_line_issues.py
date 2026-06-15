"""_build_line_issues — 라인 전체 이슈 dedup + 상태(현재/과거/되돌림) 검증."""

from app.core.tickets import extract_issue_numbers
from app.features.blame.service import _build_line_issues, _is_revert_subject


def _c(hash, subject):
    return {"hash": hash, "author": "dev", "date": "2026-06-01", "subject": subject}


def test_extract_issue_numbers_dedup_and_order():
    # 중복은 한 번으로, 순서는 보존. 지라 티켓(PAY-2041)은 세지 않는다.
    assert extract_issue_numbers("fix #12 #12 #13") == [12, 13]
    assert extract_issue_numbers("PAY-2041 작업 #7") == [7]
    assert extract_issue_numbers("이슈 없음") == []


def test_is_revert_subject_heuristic():
    assert _is_revert_subject('Revert "add #12 support"')
    assert _is_revert_subject("#12 되돌림")
    assert _is_revert_subject("롤백 처리")
    assert not _is_revert_subject("정상 커밋 #12")


def test_same_issue_across_commits_dedups_with_change_count():
    history = [
        _c("aaa", "fix billing #12"),   # 최신(current)
        _c("bbb", "add #12 support"),
        _c("ccc", "refactor for #12"),
    ]
    issues = _build_line_issues(history, "aaa")
    assert len(issues) == 1
    assert issues[0]["number"] == 12
    assert issues[0]["status"] == "current"   # 최신 커밋이 참조 → 현재
    assert issues[0]["changeCount"] == 3       # 같은 이슈 3커밋 → 한 칩, 3회


def test_past_issue_not_in_current_commit():
    history = [
        _c("aaa", "fix #12"),       # 최신
        _c("bbb", "refactor #20"),  # 과거에만
    ]
    issues = {i["number"]: i for i in _build_line_issues(history, "aaa")}
    assert issues[12]["status"] == "current"
    assert issues[20]["status"] == "past"


def test_revert_marks_reverted_unless_also_current():
    history = [
        _c("aaa", 'Revert "add #30"'),  # 최신이자 revert
        _c("bbb", "feature #40"),       # 과거
        _c("ccc", 'Revert "x #40"'),    # 과거 revert
    ]
    issues = {i["number"]: i for i in _build_line_issues(history, "aaa")}
    # #30 은 최신(=현재) 커밋이 revert 했지만, 최신 커밋 참조이므로 current 가 우선
    assert issues[30]["status"] == "current"
    # #40 은 과거 revert 커밋에만 → reverted
    assert issues[40]["status"] == "reverted"


def test_status_sort_current_first():
    history = [
        _c("aaa", "fix #1"),             # current
        _c("bbb", 'Revert "y #2"'),      # reverted
        _c("ccc", "old #3"),             # past
    ]
    order = [i["status"] for i in _build_line_issues(history, "aaa")]
    assert order == ["current", "reverted", "past"]
