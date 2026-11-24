"""Tests for the ovc command line."""

import builtins
import json
import subprocess
import sys

import pytest

from openvoicechat import cli


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "ovc" in capsys.readouterr().out


def test_doctor_json_schema(capsys):
    code = cli.main(["doctor", "--json"])
    assert code in (0, 2)
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == cli.SCHEMA_VERSION
    assert isinstance(payload["ok"], bool)
    assert payload["checks"]
    for check in payload["checks"]:
        assert set(check) == {"name", "status", "detail"}
        assert check["status"] in {"ok", "warn", "missing", "fail"}


def test_doctor_keeps_reporting_when_audio_import_cannot_initialize(monkeypatch, capsys):
    original_import = builtins.__import__

    def import_with_unavailable_audio(name, *args, **kwargs):
        if name == "sounddevice":
            raise OSError("PortAudio could not initialize")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_with_unavailable_audio)
    assert cli.main(["doctor", "--json"]) in (0, 2)
    payload = json.loads(capsys.readouterr().out)
    audio = next(check for check in payload["checks"] if check["name"] == "audio")
    assert audio["status"] == "warn"
    assert "PortAudio could not initialize" in audio["detail"]


def test_package_import_stays_lazy():
    code = "import sys, openvoicechat; print('openai' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"
