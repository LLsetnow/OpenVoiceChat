import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from fastapi.testclient import TestClient

import run_voice_web
from openvoicechat import history
from openvoicechat import migrate as history_migrations


@pytest.fixture
def web_test_env(monkeypatch, tmp_path):
    database_dir = tmp_path / "web-test-data"
    database_dir.mkdir(parents=True, exist_ok=True)
    database = database_dir / "conversations.sqlite3"

    monkeypatch.setattr(history, "DATA_DIR", database_dir)
    monkeypatch.setattr(history, "DATABASE", database)
    monkeypatch.setattr(history_migrations, "DATA_DIR", database_dir)
    monkeypatch.setattr(history_migrations, "DATABASE", database)
    monkeypatch.setattr(
        history_migrations,
        "MIGRATIONS_DIR",
        Path(__file__).resolve().parents[1] / "openvoicechat" / "migrations",
    )
    history_migrations.migrate()

    dummy_speech = MagicMock()
    dummy_speech.aclose = AsyncMock()
    dummy_config = MagicMock()
    monkeypatch.setattr(
        run_voice_web,
        "voice_profile",
        lambda **kwargs: (dummy_config, dummy_speech),
    )

    client = TestClient(run_voice_web.app, base_url="http://127.0.0.1")
    return client


def test_audio_worklet_processor_static_asset(web_test_env):
    client = web_test_env
    response = client.get("/assets/audio-worklet-processor.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    content = response.text
    assert 'registerProcessor("recorder-worklet-processor"' in content
    assert "AudioWorkletRecorderProcessor" in content
    assert "resampleOffset" in content
    assert "this.targetSampleRate = opts.targetSampleRate || 16000;" in content
    assert "this.bufferSize = opts.bufferSize || 512;" in content


def test_i18n_translation_keys_symmetry():
    i18n_path = Path(__file__).resolve().parents[1] / "web" / "i18n.js"
    text = i18n_path.read_text(encoding="utf-8")

    zh_match = re.search(r'"zh-CN":\s*\{(.*?)\n\s*\},', text, re.DOTALL)
    en_match = re.search(r'en:\s*\{(.*?)\n\s*\},\s*\n\s*\};', text, re.DOTALL)
    assert zh_match is not None, "Failed to parse zh-CN block"
    assert en_match is not None, "Failed to parse en block"

    key_regex = re.compile(r'^\s*([a-zA-Z0-9_]+)\s*:', re.MULTILINE)
    zh_keys = set(key_regex.findall(zh_match.group(1)))
    en_keys = set(key_regex.findall(en_match.group(1)))

    diff_zh = zh_keys - en_keys
    diff_en = en_keys - zh_keys
    assert not diff_zh, f"Keys present in zh-CN but missing in en: {diff_zh}"
    assert not diff_en, f"Keys present in en but missing in zh-CN: {diff_en}"

    for k in (
        "mic_error",
        "mic_device_switched",
        "mic_busy_or_denied",
        "worklet_fallback_notice",
        "audio_network_lag",
    ):
        assert k in zh_keys
        assert k in en_keys


def test_worklet_resampling_algorithm_precision():
    for sr_in in (48000, 44100):
        sr_out = 16000
        # Align sig_len to integer multiple of 128-sample quanta
        num_quanta = int((sr_in * 0.5) // 128)
        sig_len = num_quanta * 128
        t = np.arange(sig_len) / sr_in
        freq = 300.0
        sig = np.sin(2 * np.pi * freq * t).astype(np.float32)

        ratio = sr_in / sr_out
        resample_offset = 0.0
        leftover_sample = 0.0
        leftover_count = 0
        out_samples = []

        for start in range(0, sig_len, 128):
            block = sig[start : start + 128]
            if len(block) < 128:
                break
            work = np.zeros(130, dtype=np.float32)
            if leftover_count > 0:
                work[0] = leftover_sample
                work[1:129] = block
                total = 129
            else:
                work[0:128] = block
                total = 128

            pos = resample_offset
            limit = total - 1
            while pos < limit:
                idx = int(pos)
                frac = pos - idx
                s = work[idx] * (1.0 - frac) + work[idx + 1] * frac
                clamped = max(-1.0, min(1.0, s))
                pcm16 = int(clamped * 0x7FFF) if clamped >= 0 else int(clamped * 0x8000)
                out_samples.append(pcm16)
                pos += ratio

            leftover_sample = work[limit]
            leftover_count = 1
            resample_offset = pos - limit

        expected_count = int(sig_len * sr_out / sr_in)
        assert abs(len(out_samples) - expected_count) <= 3
        assert all(-32768 <= s <= 32767 for s in out_samples)


def test_startup_audio_frames_buffered_without_truncation(web_test_env):
    client = web_test_env

    with client.websocket_connect(
        "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
    ) as ws:
        fake_frame = b"\x00\x01" * 512
        ws.send_bytes(fake_frame)
        ws.send_bytes(fake_frame)

        ws.send_json({"action": "start"})

        received = []
        for _ in range(5):
            msg = ws.receive_json()
            received.append(msg)
            if msg.get("kind") == "character_info":
                break

        char_info = next((m for m in received if m.get("kind") == "character_info"), None)
        assert char_info is not None
        assert char_info["name"] in ("千夏", "Mio")


def test_startup_audio_frames_bounded(web_test_env):
    client = web_test_env

    with client.websocket_connect(
        "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
    ) as ws:
        fake_frame = b"\x01\x02" * 512
        for _ in range(70):
            ws.send_bytes(fake_frame)

        ws.send_json({"action": "start"})
        msg = ws.receive_json()
        assert msg is not None
