import pytest
from fastapi.testclient import TestClient

from openvoicechat.config import Config
from openvoicechat.conversation import Conversation
from run_voice_web import app, record_turn_event


class DummySpeech:
    async def transcribe(self, pcm: bytes) -> str:
        return "你好"

    async def synthesize(self, text: str) -> bytes:
        return b"\x00\x00" * 800

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_conversation_events_include_turn_id():
    events = []

    def on_event(kind, **data):
        events.append({"kind": kind, **data})

    conv = Conversation(
        DummySpeech(),
        config=Config(llm_api_key="mock-key"),
        client=object(),  # unused if not calling llm
        on_event=on_event,
    )

    # Test _interrupt measurement
    await conv.output.put(b"\x00" * 320)
    await conv.output.put(b"\x00" * 320)
    assert conv.output.qsize() == 2

    await conv._interrupt()
    assert conv.output.qsize() == 0

    interrupted_event = next((e for e in events if e["kind"] == "interrupted"), None)
    assert interrupted_event is not None
    assert interrupted_event["duration_ms"] >= 0
    assert interrupted_event["cleared_chunks"] == 2


def test_diagnostics_latency_api_endpoint():
    client = TestClient(app)

    # Clean up test turn metrics
    app.state.turn_metrics = []

    record_turn_event(app, "turn_start", "gpt_sovits", 8, turn_id="t1", input_type="voice")
    record_turn_event(app, "timing", "gpt_sovits", 8, turn_id="t1", stage="asr", milliseconds=150.0)
    record_turn_event(app, "timing", "gpt_sovits", 8, turn_id="t1", stage="llm_first", milliseconds=280.0)
    record_turn_event(app, "timing", "gpt_sovits", 8, turn_id="t1", stage="llm", milliseconds=490.0)
    record_turn_event(app, "timing", "gpt_sovits", 8, turn_id="t1", stage="tts", milliseconds=740.0)
    record_turn_event(app, "output_complete", "gpt_sovits", 8)

    # Simulate playback_started received from browser
    rec = app.state.turn_metrics[0]
    rec["playback_latency_ms"] = 1280.0
    rec["network_queue_ms"] = 20.0
    rec["status"] = "completed"

    resp = client.get("/api/diagnostics/latency")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "environment" in data
    assert data["summary"]["total_turns"] == 1
    assert data["summary"]["completed_turns"] == 1
    assert data["summary"]["metrics"]["playback_latency_ms"]["p50"] == 1280.0
    assert data["summary"]["metrics"]["tts_ms"]["p50"] == 740.0

    # Test status endpoint includes diagnostics link
    status_resp = client.get("/api/status")
    assert status_resp.status_code == 200
    assert status_resp.json().get("diagnostics") == "/api/diagnostics/latency"


def test_diagnostics_interruption_tracking():
    app.state.turn_metrics = []

    record_turn_event(app, "turn_start", "gpt_sovits", 4, turn_id="t2", input_type="voice")
    record_turn_event(app, "timing", "gpt_sovits", 4, turn_id="t2", stage="asr", milliseconds=120.0)
    record_turn_event(app, "interrupted", "gpt_sovits", 4, turn_id="t2", duration_ms=11.0)

    rec = app.state.turn_metrics[0]
    assert rec["status"] == "interrupted"
    assert rec["interruption_latency_ms"] == 11.0

    client = TestClient(app)
    resp = client.get("/api/diagnostics/latency")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["interrupted_turns"] == 1
    assert data["summary"]["metrics"]["interruption_latency_ms"]["p50"] == 11.0
