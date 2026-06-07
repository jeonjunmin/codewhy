"""commit_classifier — SSOT 보호.

이 모듈은 블레임과 타임라인이 공유한다(TIMELINE_FOLLOWUP §6 계약).
시그니처 회귀가 일어나지 않도록 공개 심볼·반환 키를 직접 검증한다.
"""

from app.core.commit_classifier import SKIP_TYPES, classify_commit, filter_meaningful


def test_skip_types_minimum_contract():
    # TIMELINE_FOLLOWUP §6 계약 — 이 셋은 절대 줄어들지 않아야 한다.
    assert {"test", "chore", "docs"}.issubset(SKIP_TYPES)


def test_classify_returns_lowercase_type():
    out = classify_commit({"message": "FEAT[blame]: 새 기능"})
    assert out["type"] == "feat"
    assert out["domain"] == "blame"


def test_classify_unmatched_message_becomes_other():
    out = classify_commit({"message": "그냥 평문 메시지"})
    assert out["type"] == "other"
    assert out["domain"] is None


def test_classify_uses_subject_first_then_message():
    out = classify_commit({"subject": "fix: a", "message": "feat: b"})
    assert out["type"] == "fix"


def test_classify_preserves_other_keys():
    out = classify_commit({"hash": "abc", "message": "feat: x"})
    assert out["hash"] == "abc"


def test_filter_meaningful_drops_noise():
    commits = [
        {"message": "feat: real"},
        {"message": "docs: readme"},
        {"message": "chore: bump deps"},
        {"message": "fix: bug"},
    ]
    out = filter_meaningful(commits)
    types = {c["type"] for c in out}
    assert types == {"feat", "fix"}


def test_filter_meaningful_returns_all_when_only_noise():
    # 전부 노이즈면 빈 리스트 대신 전체 그대로 — 타임라인이 fallback 으로 쓸 수 있도록
    commits = [{"message": "docs: a"}, {"message": "chore: b"}]
    out = filter_meaningful(commits)
    assert len(out) == 2
