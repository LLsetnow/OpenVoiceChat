import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import run_voice_chat
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

    last_voice_profile_kwargs = {}

    def mock_voice_profile(**kwargs):
        last_voice_profile_kwargs.update(kwargs)
        dummy_speech = MagicMock()
        dummy_speech.aclose = AsyncMock()
        dummy_config = MagicMock()
        return dummy_config, dummy_speech

    monkeypatch.setattr(run_voice_web, "voice_profile", mock_voice_profile)

    client = TestClient(run_voice_web.app, base_url="http://127.0.0.1")
    return client, last_voice_profile_kwargs


def test_i18n_translation_keys_parity():
    i18n_file = Path(__file__).resolve().parents[1] / "web" / "i18n.js"
    content = i18n_file.read_text(encoding="utf-8")

    assert '"zh-CN": {' in content
    assert 'en: {' in content
    zh_block = content.split('"zh-CN": {')[1].split('},\n    en: {')[0]
    en_block = content.split('en: {')[1].split('\n    },\n  };')[0]

    def extract_keys(block: str) -> set[str]:
        return set(re.findall(r'^\s*([a-zA-Z0-9_]+):', block, flags=re.MULTILINE))

    zh_keys = extract_keys(zh_block)
    en_keys = extract_keys(en_block)

    missing_in_en = zh_keys - en_keys
    missing_in_zh = en_keys - zh_keys

    assert not missing_in_en, f"Keys missing in en: {missing_in_en}"
    assert not missing_in_zh, f"Keys missing in zh-CN: {missing_in_zh}"

    required_keys = {
        "conv_lang_select_label",
        "conv_lang_auto",
        "conv_lang_zh",
        "conv_lang_en",
        "conv_lang_hint_auto",
        "conv_lang_hint_zh",
        "conv_lang_hint_en",
        "tts_lang_fallback",
        "tts_lang_unsupported",
        "turn_failed",
        "session_occupied",
        "missing_configuration",
        "session_runtime_error",
        "devices_scan_failed",
    }
    for key in required_keys:
        assert key in zh_keys, f"Required key {key} missing from zh-CN"
        assert key in en_keys, f"Required key {key} missing from en"


def test_normalize_language():
    assert run_voice_chat.normalize_language(None) == "auto"
    assert run_voice_chat.normalize_language("") == "auto"
    assert run_voice_chat.normalize_language("auto") == "auto"
    assert run_voice_chat.normalize_language("zh") == "zh"
    assert run_voice_chat.normalize_language("zh-CN") == "zh"
    assert run_voice_chat.normalize_language("Chinese") == "zh"
    assert run_voice_chat.normalize_language("en") == "en"
    assert run_voice_chat.normalize_language("en-US") == "en"
    assert run_voice_chat.normalize_language("English") == "en"
    assert run_voice_chat.normalize_language("unknown_lang") == "auto"


def test_inject_language_instruction():
    base_prompt = "You are an assistant."

    zh_prompt = run_voice_chat.inject_language_instruction(base_prompt, "zh")
    assert "中文" in zh_prompt
    assert "必须且只能使用自然、流利的中文" in zh_prompt

    en_prompt = run_voice_chat.inject_language_instruction(base_prompt, "en")
    assert "English" in en_prompt
    assert "strictly and entirely reply in natural, fluent English" in en_prompt

    auto_prompt = run_voice_chat.inject_language_instruction(base_prompt, "auto")
    assert "根据用户当前提问的语言对等回复" in auto_prompt


def test_resolve_tts_language():
    # GPT-SoVITS
    lang, notice = run_voice_chat.resolve_tts_language("gpt_sovits", "chinatsu", "zh")
    assert lang == "zh"
    assert notice is None

    lang, notice = run_voice_chat.resolve_tts_language("gpt_sovits", "chinatsu", "en")
    assert lang == "en"
    assert notice is None

    lang, notice = run_voice_chat.resolve_tts_language("gpt_sovits", "chinatsu", "auto")
    assert lang == "auto"
    assert notice is None

    # CosyVoice
    lang, notice = run_voice_chat.resolve_tts_language("cosyvoice", "longxiaochun_v2", "en")
    assert lang == "en"
    assert notice is None

    lang, notice = run_voice_chat.resolve_tts_language("cosyvoice", "longxiaochun_v2", "zh")
    assert lang == "zh"
    assert notice is None

    # Fallback when invalid language is forced
    lang, notice = run_voice_chat.resolve_tts_language("gpt_sovits", "chinatsu", "fr_FR")
    assert lang == "auto"
    assert notice == "tts_lang_fallback"


def test_session_websocket_language_query_param(web_test_env):
    client, last_kwargs = web_test_env
    with client.websocket_connect(
        "/session?language=en", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
    ) as ws:
        ws.send_json({"action": "start"})

        received = []
        for _ in range(6):
            msg = ws.receive_json()
            received.append(msg)
            if msg.get("kind") == "character_info":
                break

        char_info = next((m for m in received if m.get("kind") == "character_info"), None)
        assert char_info is not None
        assert char_info["language"] == "en"
        assert last_kwargs.get("language") == "en"


def test_session_action_start_language_override(web_test_env):
    client, last_kwargs = web_test_env
    with client.websocket_connect(
        "/session?language=en", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
    ) as ws:
        ws.send_json({"action": "start", "language": "zh"})

        received = []
        for _ in range(6):
            msg = ws.receive_json()
            received.append(msg)
            if msg.get("kind") == "character_info":
                break

        char_info = next((m for m in received if m.get("kind") == "character_info"), None)
        assert char_info is not None
        assert char_info["language"] == "zh"
        assert last_kwargs.get("language") == "zh"


def test_session_action_set_language_dynamically(web_test_env):
    client, _ = web_test_env
    with client.websocket_connect(
        "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
    ) as ws:
        init_state = ws.receive_json()
        assert init_state.get("kind") == "state"

        ws.send_json({"action": "set_language", "language": "en"})
        msg = ws.receive_json()
        assert msg.get("kind") == "language_changed"
        assert msg.get("language") == "en"


def test_structured_error_missing_configuration(web_test_env, monkeypatch):
    client, _ = web_test_env

    def raise_config_err(**kwargs):
        raise run_voice_chat.VoiceConfigurationError(
            "缺少环境变量：DEEPSEEK_API_KEY", missing_keys=["DEEPSEEK_API_KEY"]
        )

    monkeypatch.setattr(run_voice_web, "voice_profile", raise_config_err)

    with client.websocket_connect(
        "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
    ) as ws:
        ws.send_json({"action": "start"})

        received = []
        for _ in range(6):
            msg = ws.receive_json()
            received.append(msg)
            if msg.get("kind") == "error":
                break

        err = next((m for m in received if m.get("kind") == "error"), None)
        assert err is not None
        assert err.get("error_key") == "missing_configuration"
        assert err.get("message_key") == "missing_configuration"
        assert "DEEPSEEK_API_KEY" in err.get("missing", "")
        assert err.get("message_en") is not None


def test_structured_error_devices_scan_failed(web_test_env, monkeypatch):
    client, _ = web_test_env

    def raise_device_err(*args, **kwargs):
        raise RuntimeError("Audio hardware unavailable")

    monkeypatch.setattr(run_voice_web, "audio_devices", raise_device_err)

    with client.websocket_connect(
        "/session", headers={"host": "127.0.0.1", "origin": "http://127.0.0.1"}
    ) as ws:
        ws.send_json({"action": "devices"})

        received = []
        for _ in range(5):
            msg = ws.receive_json()
            received.append(msg)
            if msg.get("kind") == "devices_error":
                break

        dev_err = next((m for m in received if m.get("kind") == "devices_error"), None)
        assert dev_err is not None
        assert dev_err.get("error_key") == "devices_scan_failed"
        assert dev_err.get("message_key") == "devices_scan_failed"
        assert dev_err.get("message_en") is not None
