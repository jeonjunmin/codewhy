"""crud._to_response — blame_explanations(AI) + commits(메타) 합성 매핑.

ON CONFLICT 등 dialect 종속 동작은 PostgreSQL 통합 테스트로 분리(별도 TODO).
여기서는 SQLAlchemy 모델 인스턴스만 만들어 매핑 규칙을 검증한다.
"""

from datetime import date

from app.db.models import BlameExplanation, Commit
from app.features.blame.crud import _to_response


def _commit(**overrides):
    base = dict(
        commit_hash="a" * 40,
        author="홍길동",
        committed_date=date(2026, 3, 15),
        message="feat: x",
        ticket="PAY-2041",
    )
    base.update(overrides)
    return Commit(**base)


def _explanation(**overrides):
    base = dict(
        file_id=1,
        commit_id=1,
        explanation="비즈니스 사유 한 문장",
        ai_suggestion="다음에 고려할 점",
        source_ref="Issue #12: 결제 취소 정책",
        issue_url="https://github.com/o/r/issues/12",
        attachments=[{"label": "spec.pdf", "url": "https://x/spec.pdf"}],
        change_stats={"added": 5, "removed": 1},
        pr_info={"url": "https://github.com/o/r/pull/3", "lines": 23},
        related_changes=[{"kind": "doc", "title": "Issue #12", "meta": "연관 이슈"}],
    )
    base.update(overrides)
    return BlameExplanation(**base)


def test_maps_all_ai_fields():
    out = _to_response(_explanation(), _commit())
    assert out["explanation"] == "비즈니스 사유 한 문장"
    assert out["aiSuggestion"] == "다음에 고려할 점"
    assert out["sourceRef"] == "Issue #12: 결제 취소 정책"
    # specRef 는 sourceRef 와 동일 (점진 도입 기간의 별칭)
    assert out["specRef"] == out["sourceRef"]
    assert out["issueUrl"].endswith("/issues/12")
    assert out["attachments"][0]["label"] == "spec.pdf"
    assert out["changeStats"]["added"] == 5
    assert out["prInfo"]["lines"] == 23
    assert out["relatedChanges"][0]["kind"] == "doc"


def test_maps_commit_metadata():
    out = _to_response(_explanation(), _commit())
    assert out["commitHash"] == "a" * 40
    assert out["author"] == "홍길동"
    # date 는 ISO 문자열로 변환
    assert out["date"] == "2026-03-15"
    assert out["ticket"] == "PAY-2041"


def test_missing_date_yields_empty_string():
    out = _to_response(_explanation(), _commit(committed_date=None))
    assert out["date"] == ""


def test_null_collections_become_empty_lists():
    # attachments/related_changes 가 None 으로 들어와도 응답은 [] 로 정규화
    out = _to_response(_explanation(attachments=None, related_changes=None), _commit())
    assert out["attachments"] == []
    assert out["relatedChanges"] == []


def test_team_filled_via_team_map(monkeypatch):
    # get_team_map 결과를 mock 해 commit.author → team 매핑 확인
    import app.features.blame.crud as crud_mod
    monkeypatch.setattr(crud_mod, "get_team_map", lambda: {"홍길동": "결제팀"})
    out = _to_response(_explanation(), _commit())
    assert out["team"] == "결제팀"


def test_unmapped_author_yields_none_team(monkeypatch):
    import app.features.blame.crud as crud_mod
    monkeypatch.setattr(crud_mod, "get_team_map", lambda: {})
    out = _to_response(_explanation(), _commit(author="알 수 없는 사람"))
    assert out["team"] is None
