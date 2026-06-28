"""refine_titles — 미적중 커밋만 다듬어 {hash: title} 반환 (DB 캐시·Bedrock 은 호출부/네트워크 책임).

실제 Bedrock 대신 service._refine_titles 를 monkeypatch 로 가로채, 순수 zip/필터 로직만 검증한다.
"""

from app.features.blame import service


def test_empty_commits_returns_empty(monkeypatch):
    called = False

    def fake(_commits):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(service, "_refine_titles", fake)
    assert service.refine_titles([]) == {}
    assert called is False  # 다듬을 게 없으면 Bedrock 을 부르지 않는다


def test_maps_hash_to_refined_title_in_order(monkeypatch):
    monkeypatch.setattr(service, "_refine_titles", lambda commits: ["수수료율 3% 적용", "모듈 초기 추가"])
    out = service.refine_titles([
        {"hash": "aaa", "subject": "#57 문구 수정"},
        {"hash": "bbb", "subject": "feat: 모듈 추가"},
    ])
    assert out == {"aaa": "수수료율 3% 적용", "bbb": "모듈 초기 추가"}


def test_skips_commits_without_message_and_drops_empty_titles(monkeypatch):
    # subject 없는 커밋은 todo 에서 빠지고, 빈 타이틀 결과는 결과 dict 에서 제외된다.
    seen = {}

    def fake(commits):
        seen["hashes"] = [c["hash"] for c in commits]
        return ["", "정상 타이틀"]

    monkeypatch.setattr(service, "_refine_titles", fake)
    out = service.refine_titles([
        {"hash": "aaa", "subject": "  "},          # 메시지 없음 → todo 제외
        {"hash": "bbb", "subject": "원본1"},
        {"hash": "ccc", "subject": "원본2"},
    ])
    assert seen["hashes"] == ["bbb", "ccc"]
    assert out == {"ccc": "정상 타이틀"}             # 빈 타이틀(bbb)은 빠짐 → 호출부가 원본 폴백


def test_bedrock_failure_returns_empty(monkeypatch):
    def boom(_commits):
        raise RuntimeError("bedrock down")

    monkeypatch.setattr(service, "_refine_titles", boom)
    # 실패해도 예외를 삼키고 빈 dict → 호출부가 원본 메시지로 폴백한다.
    assert service.refine_titles([{"hash": "aaa", "subject": "원본"}]) == {}
