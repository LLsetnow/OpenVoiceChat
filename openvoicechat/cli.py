"""The ``ovc`` command line.

Only ``doctor`` exists so far. ``init``, ``models``, ``run``, ``bench``, and
``serve`` land as the runtime is built out.

Imports stay lazy: ``ovc --help`` and ``ovc --version`` must not load numpy,
onnxruntime, torch, or any model code.
"""

from __future__ import annotations

import argparse
import json
import platform
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("openvoicechat")
except PackageNotFoundError:  # running from a source checkout
    __version__ = "0.4.1"

SCHEMA_VERSION = 1
_LABEL = {"ok": "PASS", "warn": "WARN", "missing": "MISSING", "fail": "FAIL"}


@dataclass
class Check:
    name: str
    status: str  # ok | warn | missing | fail
    detail: str


def _python() -> Check:
    return Check("python", "ok", f"{platform.python_version()} ({platform.system()})")


def _torch() -> Check:
    try:
        import torch
    except ImportError:
        return Check("torch", "missing", 'pip install "openvoicechat[models]"')
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        vram = props.total_memory / 1024**3
        return Check("torch", "ok", f"{torch.__version__} | CUDA: {props.name} ({vram:.1f} GB)")
    return Check("torch", "warn", f"{torch.__version__} | CPU only; Kokoro will be slow")


def _onnxruntime() -> Check:
    try:
        import onnxruntime as ort
    except ImportError:
        return Check("onnxruntime", "fail", "not installed; reinstall openvoicechat")
    providers = ort.get_available_providers()
    if "CUDAExecutionProvider" in providers:
        return Check("onnxruntime", "ok", f"{ort.__version__} | CUDAExecutionProvider")
    joined = ", ".join(providers) or "none"
    return Check("onnxruntime", "warn", f"{ort.__version__} | CPU only ({joined})")


def _module(name: str, check: str, hint: str) -> Check:
    try:
        __import__(name)
    except ImportError:
        return Check(check, "missing", hint)
    return Check(check, "ok", f"import {name}")


def _audio() -> Check:
    try:
        import sounddevice as sd
    except ImportError:
        return Check("audio", "missing", 'pip install "openvoicechat[local]"')
    except Exception as exc:  # noqa: BLE001 - PortAudio can fail during import
        return Check("audio", "warn", f"sounddevice could not initialize: {exc}")
    try:
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001 - PortAudio raises its own types
        return Check("audio", "warn", f"sounddevice present, no device list: {exc}")
    inputs = sum(1 for device in devices if device["max_input_channels"] > 0)
    outputs = sum(1 for device in devices if device["max_output_channels"] > 0)
    status = "ok" if inputs and outputs else "warn"
    return Check("audio", status, f"{inputs} input / {outputs} output device(s)")


def _silero() -> Check:
    from .vad import MODEL_PATH

    if MODEL_PATH.exists():
        return Check("silero", "ok", f"{MODEL_PATH} ({MODEL_PATH.stat().st_size // 1024} KB)")
    return Check("silero", "warn", "downloads on first run (~2 MB)")


def _llm() -> Check:
    from .config import Config

    config = Config()
    if not config.llm_api_key:
        return Check("llm", "fail", "set LLM_API_KEY (any value works for local servers)")
    base = config.llm_base_url or "https://api.openai.com/v1"
    return Check("llm", "ok", f"{config.llm_model} @ {base}")


def _dashscope() -> Check:
    import os

    api_key = (os.getenv("DASHSCOPE_API_KEY") or os.getenv("ALIYUN_API_KEY") or "").strip()
    if not api_key:
        return Check(
            "dashscope",
            "missing",
            "set DASHSCOPE_API_KEY or ALIYUN_API_KEY in .env or environment",
        )
    masked = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "***"
    return Check("dashscope", "ok", f"configured ({masked})")


def _web_llm() -> Check:
    import os

    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    llm_key = os.getenv("LLM_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    if deepseek_key:
        masked = deepseek_key[:4] + "..." + deepseek_key[-4:] if len(deepseek_key) > 8 else "***"
        return Check("llm", "ok", f"DeepSeek configured ({masked})")
    if llm_key:
        masked = llm_key[:4] + "..." + llm_key[-4:] if len(llm_key) > 8 else "***"
        return Check("llm", "ok", f"LLM key configured ({masked})")
    return Check("llm", "missing", "set DEEPSEEK_API_KEY (recommended) or LLM_API_KEY in .env")


def _sovits(sovits_url: str | None = None) -> Check:
    import json
    import os
    import urllib.error
    import urllib.request

    from .config import Config

    target_url = (sovits_url or os.getenv("SOVITS_URL") or Config().sovits_url).rstrip("/")
    endpoint = f"{target_url}/voices"
    req = urllib.request.Request(endpoint, headers={"User-Agent": "OpenVoiceChat-Doctor"})
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if resp.status == 200:
                body = json.loads(resp.read().decode("utf-8"))
                voices = body.get("voices", [])
                count = len(voices) if isinstance(voices, list) else 0
                return Check("sovits", "ok", f"{target_url} | ready ({count} voice(s) available)")
            return Check("sovits", "warn", f"{target_url} returned HTTP {resp.status}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", str(exc))
        return Check(
            "sovits",
            "warn",
            f"cannot connect to {target_url}/voices: {reason} (start AiServer or switch to CosyVoice)",
        )
    except Exception as exc:  # noqa: BLE001 - catch unexpected probe errors
        return Check("sovits", "warn", f"error probing {target_url}: {exc}")


def _database() -> Check:
    import sqlite3

    from .history import DATA_DIR, DATABASE

    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Check("database", "fail", f"cannot create data directory {DATA_DIR}: {exc}")

    test_file = DATA_DIR / ".doctor_write_test"
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
    except OSError as exc:
        return Check("database", "fail", f"data directory {DATA_DIR} is not writable: {exc}")

    if not DATABASE.is_file():
        return Check(
            "database",
            "warn",
            f"{DATABASE} does not exist (created automatically on web start, or run 'python -m openvoicechat.migrate')",
        )

    try:
        with sqlite3.connect(DATABASE, timeout=3) as conn:
            cur = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
            version = cur.fetchone()[0]
        return Check("database", "ok", f"{DATABASE} | ready (migration version: {version})")
    except Exception as exc:  # noqa: BLE001 - sqlite3 operational or query errors
        return Check("database", "fail", f"cannot query {DATABASE}: {exc}")


def collect_checks() -> list[Check]:
    return [
        _python(),
        _torch(),
        _onnxruntime(),
        _module("onnx_asr", "parakeet", 'pip install "openvoicechat[models]"'),
        _module("kokoro", "kokoro", 'pip install "openvoicechat[models]"'),
        _audio(),
        _silero(),
        _llm(),
    ]


def collect_web_checks(sovits_url: str | None = None) -> list[Check]:
    return [
        _python(),
        _dashscope(),
        _web_llm(),
        _sovits(sovits_url),
        _database(),
    ]


def doctor(as_json: bool, mode: str = "local", sovits_url: str | None = None) -> int:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
    if mode == "web":
        checks = collect_web_checks(sovits_url=sovits_url)
    else:
        checks = collect_checks()
    ok = not any(check.status in ("missing", "fail") for check in checks)
    if as_json:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "mode": mode,
            "ok": ok,
            "checks": [asdict(check) for check in checks],
        }
        print(json.dumps(payload, indent=2))
    else:
        for check in checks:
            print(f"{_LABEL[check.status]:<8} {check.name:<12} {check.detail}")
        print()
        print("Ready." if ok else "Not ready: fix the missing or failed checks above.")
    return 0 if ok else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ovc",
        description="OpenVoiceChat: local voice runtime for Python.",
    )
    parser.add_argument("--version", action="version", version=f"ovc {__version__}")
    subcommands = parser.add_subparsers(dest="command")
    check = subcommands.add_parser("doctor", help="check hardware, models, and configuration")
    check.add_argument("--json", action="store_true", help="machine-readable output")
    check.add_argument(
        "--mode",
        choices=("local", "web"),
        default="local",
        help="diagnosis mode: local (default) or web (Mac/cloud web mode)",
    )
    check.add_argument(
        "--web",
        action="store_true",
        help="shorthand for --mode web",
    )
    check.add_argument(
        "--sovits-url",
        default=None,
        help="override GPT-SoVITS URL for web mode check",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        mode = "web" if args.web else args.mode
        return doctor(args.json, mode=mode, sovits_url=args.sovits_url)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
