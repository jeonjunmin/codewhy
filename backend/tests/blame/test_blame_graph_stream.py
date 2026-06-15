"""stream_blame_graph — SSE 프레임 시퀀스(meta/delta/done) + degraded 폴백 검증.

성공 경로는 langchain GenericFakeChatModel 로 실제 on_chat_model_stream 이벤트를 발생시켜
astream_events 기반 토큰 스트리밍이 delta 로 흐르는지 확인한다.
"""

import asyncio
import json

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app.ai import blame_graph as bg
from app.core import git
from app.features.blame import service as svc


def _info() -> git.BlameInfo:
    return git.BlameInfo(
        commit_hash="b" * 40, author="개발자A", date="2026-01-02",
        message="feat[payment]: 결제 취소 추가", diff="@@ -1 +1 @@\n-a\n+b", added=1, removed=1,
    )


def _collect(gen_factory):
    async def _run():
        frames = []
        async for frame in gen_factory():
            assert frame.startswith("data: ") and frame.endswith("\n\n")
            frames.append(json.loads(frame[len("data: "):].strip()))
        return frames
    return asyncio.run(_run())


def _patch_no_external(monkeypatch):
    monkeypatch.setattr(svc, "_build_line_history", lambda *a, **k: [])
    monkeypatch.setattr(svc, "_safe_find_pr", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_safe_find_issues", lambda *a, **k: [])
    monkeypatch.setattr(git, "find_followup_commits", lambda *a, **k: [])


def test_stream_emits_meta_delta_done(monkeypatch):
    _patch_no_external(monkeypatch)
    # GenericFakeChatModel 은 메시지를 토큰 단위로 스트리밍하며 astream_events 에 참여한다.
    # explain 노드가 정확히 1회만 실행되는지도 함께 검증한다(fan-in 중복 실행 회귀 방지).
    llm_calls = {"n": 0}

    def _factory(*a, **k):
        llm_calls["n"] += 1
        return GenericFakeChatModel(messages=iter([AIMessage(content="결제 취소 정책 변경")]))

    monkeypatch.setattr(bg, "get_bedrock_llm", _factory)

    frames = _collect(lambda: bg.stream_blame_graph(
        None, "/repo", "pay.py", 10,
        info=_info(), branch="main", ticket="PAY-1", commit=None, file=None,
    ))

    assert llm_calls["n"] == 1, "explain(=Bedrock 호출)은 정확히 1회여야 한다"
    assert "meta" in frames[0]
    assert frames[0]["meta"]["commitHash"] == "b" * 40
    deltas = [f["delta"] for f in frames if "delta" in f]
    assert deltas, "토큰 delta 가 하나 이상 있어야 한다"
    assert "".join(deltas) == "결제 취소 정책 변경"
    done = frames[-1]
    assert done.get("done") is True
    assert done["explanation"] == "결제 취소 정책 변경"
    assert done["aiDegraded"] is False


class _BoomLLM:
    def with_config(self, *_, **__):
        return self

    async def ainvoke(self, _messages):
        raise RuntimeError("Bedrock down")


def test_stream_degraded_emits_single_delta_and_no_cache(monkeypatch):
    _patch_no_external(monkeypatch)
    monkeypatch.setattr(bg, "get_bedrock_llm", lambda *a, **k: _BoomLLM())

    saved = {"called": False}

    async def _save(*a, **k):
        saved["called"] = True

    monkeypatch.setattr(bg.crud, "save_blame", _save)

    # commit/file 이 있어도 degraded 면 캐시에 저장하지 않아야 한다
    class _Row:
        id = 1

    frames = _collect(lambda: bg.stream_blame_graph(
        None, "/repo", "pay.py", 10,
        info=_info(), branch="main", ticket="PAY-1", commit=_Row(), file=_Row(),
    ))

    done = frames[-1]
    assert done["aiDegraded"] is True
    deltas = [f["delta"] for f in frames if "delta" in f]
    assert len(deltas) == 1               # 폴백 문구 한 번만
    assert "AI 설명" in deltas[0]
    assert saved["called"] is False       # degraded → 캐시 미저장
