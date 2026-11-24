"""Tests for unified voice service configuration and doctor web mode diagnostics."""

import io
import json
import sqlite3
import urllib.error
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from openvoicechat import cli
from openvoicechat.aliyun_sovits import AliyunSpeechPipeline
from openvoicechat.config import DEFAULT_SOVITS_URL, Config
from run_voice_web import app


def test_config_sovits_url_defaults_and_env(monkeypatch):
    monkeypatch.delenv("SOVITS_URL", raising=False)
    config = Config()
    assert config.sovits_url == DEFAULT_SOVITS_URL
    assert config.sovits_url == "http://127.0.0.1:19889"

    monkeypatch.setenv("SOVITS_URL", "http://192.168.1.100:19889/")
    config_env = Config()
    assert config_env.sovits_url == "http://192.168.1.100:19889"

    config_override = Config(sovits_url="http://custom-host:9999/")
    assert config_override.sovits_url == "http://custom-host:9999"


@pytest.mark.asyncio
async def test_aliyun_sovits_uses_unified_url(monkeypatch):
    monkeypatch.setenv("SOVITS_URL", "http://env-sovits:19889")
    pipeline = AliyunSpeechPipeline(
        "test_api_key",
        voice="chinatsu",
        language="zh",
    )
    assert pipeline.sovits_url == "http://env-sovits:19889"

    pipeline_custom = AliyunSpeechPipeline(
        "test_api_key",
        voice="chinatsu",
        language="zh",
        sovits_url="http://custom-sovits:18000/",
    )
    assert pipeline_custom.sovits_url == "http://custom-sovits:18000"

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"RIFF" + b"\x00" * 36
    mock_response.raise_for_status = MagicMock()

    # Mock wav decode
    fake_wav = MagicMock()
    fake_wav.__enter__.return_value = fake_wav
    fake_wav.getsampwidth.return_value = 2
    fake_wav.getframerate.return_value = 16000
    fake_wav.getnchannels.return_value = 1
    fake_wav.getnframes.return_value = 100
    fake_wav.readframes.return_value = b"\x00\x00" * 100

    with (
        patch.object(pipeline_custom._tts, "post", new_callable=AsyncMock) as mock_post,
        patch("wave.open", return_value=fake_wav),
    ):
        mock_post.return_value = mock_response
        pcm = await pipeline_custom._synthesize_sovits("你好，世界")
        assert len(pcm) > 0
        mock_post.assert_awaited_once()
        call_url = mock_post.await_args[0][0]
        assert call_url == "http://custom-sovits:18000/"


def test_web_status_and_voices_endpoint_unified_sovits(monkeypatch):
    monkeypatch.setenv("SOVITS_URL", "http://10.0.0.5:19889")
    app.state.sovits_url = None

    with TestClient(app) as client:
        status_resp = client.get("/api/status")
        assert status_resp.status_code == 200
        data = status_resp.json()
        assert data["sovits_url"] == "http://10.0.0.5:19889"

        # Test api/voices reads from configured sovits_url
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "voices": [{"id": "custom_v", "name": "自定义音色"}]
            }
            mock_get.return_value = mock_resp

            voices_resp = client.get("/api/voices")
            assert voices_resp.status_code == 200
            v_data = voices_resp.json()
            assert v_data["gpt_sovits"] == [{"id": "custom_v", "name": "自定义音色"}]
            mock_get.assert_awaited_once_with("http://10.0.0.5:19889/voices")


def test_doctor_web_mode_json_schema(capsys, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dashscope-secret-123456")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-secret-123456")

    code = cli.main(["doctor", "--mode", "web", "--json"])
    assert code in (0, 2)
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert payload["schema_version"] == cli.SCHEMA_VERSION
    assert payload["mode"] == "web"
    assert isinstance(payload["ok"], bool)
    assert isinstance(payload["checks"], list)

    check_names = [check["name"] for check in payload["checks"]]
    assert "python" in check_names
    assert "dashscope" in check_names
    assert "llm" in check_names
    assert "sovits" in check_names
    assert "database" in check_names

    for check in payload["checks"]:
        assert set(check) == {"name", "status", "detail"}
        assert check["status"] in {"ok", "warn", "missing", "fail"}


def test_doctor_web_flag_alias(capsys):
    code = cli.main(["doctor", "--web", "--json"])
    assert code in (0, 2)
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "web"


def test_doctor_dashscope_check_missing_and_ok(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("ALIYUN_API_KEY", raising=False)
    check_missing = cli._dashscope()
    assert check_missing.status == "missing"
    assert "DASHSCOPE_API_KEY" in check_missing.detail

    monkeypatch.setenv("ALIYUN_API_KEY", "sk-aliyun-token-8888")
    check_ok = cli._dashscope()
    assert check_ok.status == "ok"
    assert "sk-a...8888" in check_ok.detail


def test_doctor_web_llm_check(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    check_missing = cli._web_llm()
    assert check_missing.status == "missing"

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-9999")
    check_deepseek = cli._web_llm()
    assert check_deepseek.status == "ok"
    assert "DeepSeek" in check_deepseek.detail

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "sk-openai-compatible-1234")
    check_llm = cli._web_llm()
    assert check_llm.status == "ok"
    assert "LLM key" in check_llm.detail


def test_doctor_sovits_check_success_and_failure(monkeypatch):
    fake_response = io.BytesIO(b'{"voices": [{"id": "v1"}, {"id": "v2"}]}')
    fake_response.status = 200

    with patch("urllib.request.urlopen", return_value=fake_response):
        check_ok = cli._sovits("http://127.0.0.1:19889")
        assert check_ok.status == "ok"
        assert "2 voice(s)" in check_ok.detail

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
        check_err = cli._sovits("http://unreachable-host:19889")
        assert check_err.status == "warn"
        assert "cannot connect" in check_err.detail
        assert "unreachable-host:19889" in check_err.detail


def test_doctor_database_check(tmp_path, monkeypatch):
    fake_data_dir = tmp_path / "ovc_data"
    fake_db = fake_data_dir / "conversations.sqlite3"

    monkeypatch.setattr("openvoicechat.history.DATA_DIR", fake_data_dir)
    monkeypatch.setattr("openvoicechat.history.DATABASE", fake_db)

    # Directory created, DB doesn't exist yet -> warn
    check_warn = cli._database()
    assert check_warn.status == "warn"
    assert "does not exist" in check_warn.detail

    # Initialize database with migrations table
    with sqlite3.connect(fake_db) as conn:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_migrations (version) VALUES (2)")

    check_ok = cli._database()
    assert check_ok.status == "ok"
    assert "migration version: 2" in check_ok.detail

    # Test directory write failure
    with patch("pathlib.Path.mkdir", side_effect=OSError("Permission denied")):
        check_fail = cli._database()
        assert check_fail.status == "fail"
        assert "Permission denied" in check_fail.detail


def test_doctor_web_text_output(capsys, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test-dash")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deep")

    fake_response = io.BytesIO(b'{"voices": [{"id": "v1"}]}')
    fake_response.status = 200

    with patch("urllib.request.urlopen", return_value=fake_response):
        code = cli.main(["doctor", "--web"])
        out = capsys.readouterr().out
        assert "dashscope" in out
        assert "sovits" in out
        assert "database" in out
        assert code in (0, 2)
