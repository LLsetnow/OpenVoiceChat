"""Serve the local OpenVoiceChat controls and captions over a WebSocket."""

import argparse
import asyncio
import contextlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.websockets import WebSocketDisconnected

from openvoicechat.aliyun_sovits import TTS_SAMPLE_STEPS, TTS_VOICE
from openvoicechat.audio import SPEECH_FRAME_BYTES, audio_devices
from openvoicechat.benchmark import calculate_statistics, collect_environment_info
from openvoicechat.character import CharacterConfig, get_default_character, list_characters
from openvoicechat.conversation import Conversation
from openvoicechat.history import (
    append_message,
    create_conversation,
    get_conversation,
    list_conversations,
    update_conversation_settings,
    update_conversation_summary,
)
from openvoicechat.migrate import DatabaseMigrationError, migrate
from run_voice_chat import DEFAULT_PROMPT, VoiceConfigurationError, voice_profile

WEB_DIR = Path(__file__).parent / "web"
PORT = 18768
log = logging.getLogger(__name__)
audio_in_use = asyncio.Lock()
active_session_stop = None


def record_turn_event(
    target_app: FastAPI, kind: str, engine: str, steps: int, **data: Any
) -> None:
    metrics = getattr(target_app.state, "turn_metrics", None)
    if metrics is None:
        metrics = []
        target_app.state.turn_metrics = metrics

    if kind == "turn_start":
        turn_id = data.get("turn_id")
        rec = {
            "turn_id": turn_id,
            "timestamp": time.time(),
            "input_type": data.get("input_type", "voice"),
            "status": "pending",
            "asr_ms": None,
            "llm_first_ms": None,
            "llm_ms": None,
            "tts_ms": None,
            "network_queue_ms": None,
            "playback_latency_ms": None,
            "interruption_latency_ms": None,
            "tts_engine": engine,
            "tts_steps": steps,
        }
        metrics.append(rec)
        if len(metrics) > 100:
            del metrics[: len(metrics) - 100]
    elif kind == "timing":
        turn_id = data.get("turn_id")
        stage = data.get("stage")
        ms = data.get("milliseconds")
        rec = next((r for r in reversed(metrics) if r.get("turn_id") == turn_id), None)
        if rec and ms is not None:
            if stage == "asr":
                rec["asr_ms"] = float(ms)
            elif stage == "llm_first":
                rec["llm_first_ms"] = float(ms)
            elif stage == "llm":
                rec["llm_ms"] = float(ms)
            elif stage == "tts":
                rec["tts_ms"] = float(ms)
    elif kind == "interrupted":
        turn_id = data.get("turn_id")
        dur = data.get("duration_ms")
        rec = next((r for r in reversed(metrics) if r.get("turn_id") == turn_id), None)
        if rec:
            rec["status"] = "interrupted"
            if dur is not None:
                rec["interruption_latency_ms"] = float(dur)
    elif kind == "output_complete":
        if metrics and metrics[-1].get("status") == "pending":
            metrics[-1]["status"] = "completed"
    elif kind == "error" and metrics:
        metrics[-1]["status"] = "failed"


def run_startup_migrations(target_app: FastAPI) -> dict:
    try:
        applied = migrate()
        status = {
            "status": "ready",
            "applied": applied,
            "error": None,
            "message_zh": "数据库就绪",
            "message_en": "Database ready",
        }
        target_app.state.database_status = status
        target_app.state.migration_error = None
        if applied:
            log.info("Applied conversation database migrations on startup: %s", applied)
        else:
            log.info("Conversation database is up to date on startup.")
        return status
    except (DatabaseMigrationError, OSError, RuntimeError) as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        status = {
            "status": "error",
            "applied": [],
            "error": error_msg,
            "recovery": "python -m openvoicechat.migrate",
            "message_zh": "数据库迁移失败，请检查写入权限后运行 python -m openvoicechat.migrate 手动恢复。",
            "message_en": "Database migration failed. Please check write permissions and run python -m openvoicechat.migrate to recover.",
        }
        target_app.state.database_status = status
        target_app.state.migration_error = error_msg
        log.error(
            "Failed to apply database migrations on startup: %s. Recovery: python -m openvoicechat.migrate",
            exc,
        )
        return status


def ensure_database_ready(target_app: FastAPI) -> dict:
    status = getattr(target_app.state, "database_status", None)
    if status is None or status.get("status") == "error":
        status = run_startup_migrations(target_app)
    return status


@contextlib.asynccontextmanager
async def lifespan(target_app: FastAPI):
    run_startup_migrations(target_app)
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.state.tts_steps = TTS_SAMPLE_STEPS
app.state.database_status = None
app.state.migration_error = None
app.state.turn_metrics = []
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost|testserver)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


@app.get("/")
async def index():
    return FileResponse(
        WEB_DIR / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


@app.get("/api/characters")
async def api_characters():
    return {"characters": list_characters(), "default": get_default_character().__dict__}


@app.get("/api/status")
async def api_status():
    status = ensure_database_ready(app)
    return {
        "status": "ready" if status.get("status") == "ready" else "degraded",
        "database": status,
        "diagnostics": "/api/diagnostics/latency",
    }


@app.get("/api/diagnostics/latency")
async def api_diagnostics_latency():
    records = getattr(app.state, "turn_metrics", [])
    completed_records = [r for r in records if r.get("status") == "completed"]

    def get_stat(key: str) -> dict:
        vals = [r[key] for r in completed_records if r.get(key) is not None]
        return calculate_statistics(vals)

    summary = {
        "total_turns": len(records),
        "completed_turns": len(completed_records),
        "cancelled_turns": len([r for r in records if r.get("status") == "cancelled"]),
        "failed_turns": len([r for r in records if r.get("status") == "failed"]),
        "interrupted_turns": len([r for r in records if r.get("status") == "interrupted"]),
        "metrics": {
            "asr_ms": get_stat("asr_ms"),
            "llm_first_ms": get_stat("llm_first_ms"),
            "llm_ms": get_stat("llm_ms"),
            "tts_ms": get_stat("tts_ms"),
            "network_queue_ms": get_stat("network_queue_ms"),
            "playback_latency_ms": get_stat("playback_latency_ms"),
            "interruption_latency_ms": calculate_statistics(
                [
                    r["interruption_latency_ms"]
                    for r in records
                    if r.get("interruption_latency_ms") is not None
                ]
            ),
        },
    }
    return {
        "status": "ok",
        "environment": collect_environment_info(),
        "summary": summary,
        "recent_turns": records[-30:],
    }


@app.get("/api/conversations")
async def api_conversations():
    status = ensure_database_ready(app)
    if status.get("status") == "error":
        raise HTTPException(
            status_code=503,
            detail={
                "error": "database_migration_failed",
                "message_key": "db_migration_failed",
                "message": status.get(
                    "message_zh",
                    "数据库迁移失败，请检查写入权限后运行 python -m openvoicechat.migrate 手动恢复。",
                ),
                "message_en": status.get(
                    "message_en",
                    "Database migration failed. Please check write permissions and run python -m openvoicechat.migrate to recover.",
                ),
                "recovery": status.get("recovery", "python -m openvoicechat.migrate"),
                "detail": status.get("error"),
            },
        )
    return {"conversations": list_conversations()}


@app.get("/api/conversations/{conversation_id}")
async def api_conversation(conversation_id: str):
    status = ensure_database_ready(app)
    if status.get("status") == "error":
        raise HTTPException(
            status_code=503,
            detail={
                "error": "database_migration_failed",
                "message_key": "db_migration_failed",
                "message": status.get(
                    "message_zh",
                    "数据库迁移失败，请检查写入权限后运行 python -m openvoicechat.migrate 手动恢复。",
                ),
                "message_en": status.get(
                    "message_en",
                    "Database migration failed. Please check write permissions and run python -m openvoicechat.migrate to recover.",
                ),
                "recovery": status.get("recovery", "python -m openvoicechat.migrate"),
                "detail": status.get("error"),
            },
        )
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation_not_found")
    return conversation


@app.get("/api/voices")
async def api_voices():
    # Return available voices for different TTS engines
    import httpx

    gpt_sovits_voices = []
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get("http://127.0.0.1:19889/voices")
            if resp.status_code == 200:
                gpt_sovits_voices = resp.json().get("voices", [])
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("Could not load GPT-SoVITS voices: %s", exc)
    if not gpt_sovits_voices:
        gpt_sovits_voices = [
            {"id": "chinatsu", "name": "千夏"},
            {"id": "ling", "name": "铃"},
            {"id": "zhe", "name": "哲"},
            {"id": "nangongyu", "name": "南宫羽"},
            {"id": "remielle", "name": "雷米埃尔"},
        ]
    return {
        "gpt_sovits": gpt_sovits_voices,
        "cosyvoice": [
            {"id": "longxiaochun_v2", "name": "龙小淳 (知性积极)"},
            {"id": "longyue_v2", "name": "龙悦 (温暖磁性)"},
            {"id": "longhua_v2", "name": "龙华 (元气甜美)"},
            {"id": "longfeifei_v2", "name": "龙菲菲 (甜美娇气)"},
            {"id": "longwan_v2", "name": "龙婉 (细腻柔声)"},
        ],
    }


@app.websocket("/session")
async def session(websocket: WebSocket):
    origin = websocket.headers.get("origin") or ""
    host = websocket.headers.get("host") or ""
    allowed = origin == f"http://{host}" or origin.startswith(
        ("http://127.0.0.1:", "http://localhost:")
    )
    if not allowed:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    send_lock = asyncio.Lock()
    events: asyncio.Queue[dict] = asyncio.Queue()
    voice_task: asyncio.Task | None = None
    output_task: asyncio.Task | None = None
    conversation: Conversation | None = None
    input_buffer = bytearray()

    async def safe_send_json(data: dict) -> None:
        async with send_lock:
            await websocket.send_json(data)

    async def safe_send_bytes(data: bytes) -> None:
        async with send_lock:
            await websocket.send_bytes(data)

    def publish(kind: str, **data) -> None:
        events.put_nowait({"kind": kind, **data})

    async def send_events() -> None:
        while True:
            await safe_send_json(await events.get())

    async def forward_audio_output(conv: Conversation) -> None:
        try:
            while True:
                pcm = await conv.output.get()
                if pcm is None:
                    record_turn_event(websocket.app, "output_complete", "", 0)
                    publish("output_complete")
                    continue
                await safe_send_bytes(pcm)
        except asyncio.CancelledError:
            pass

    async def stop_voice() -> None:
        global active_session_stop
        if active_session_stop is stop_voice:
            active_session_stop = None
        nonlocal voice_task, output_task, conversation, input_buffer
        input_buffer.clear()
        if output_task is not None:
            output_task.cancel()
            await asyncio.gather(output_task, return_exceptions=True)
            output_task = None
        if conversation is not None:
            conversation.stop()
            with contextlib.suppress(Exception):
                await conversation.input.put(None)
        if voice_task is not None:
            voice_task.cancel()
            await asyncio.gather(voice_task, return_exceptions=True)
            voice_task = None
        conversation = None

    def on_turn(conversation_id: str, role: str, text: str) -> None:
        if role == "error":
            publish("error", message="本轮对话失败，请再说一次。")
        else:
            try:
                append_message(conversation_id, role, text)
            except (DatabaseMigrationError, OSError, RuntimeError) as exc:
                log.error("Failed to append message to conversation: %s", exc)
            publish("message", role=role, text=text)

    async def start_voice(
        barge_in: bool,
        character_id: str | None = None,
        voice_override: str | None = None,
        engine_override: str | None = None,
        steps_override: int | None = None,
        conversation_id: str | None = None,
        initial_text: str | None = None,
        initial_speak_text: str | None = None,
    ) -> None:
        global active_session_stop
        nonlocal voice_task, output_task, conversation
        if audio_in_use.locked():
            if active_session_stop is not None and active_session_stop is not stop_voice:
                log.info("Stopping prior active voice session...")
                await active_session_stop()
                for _ in range(25):
                    if not audio_in_use.locked():
                        break
                    await asyncio.sleep(0.04)
            if audio_in_use.locked():
                publish("error", message="对话正在另一个页面使用，请先在那里停止。")
                publish("state", state="idle")
                return
        active_session_stop = stop_voice

        db_status = ensure_database_ready(websocket.app)
        if db_status.get("status") == "error":
            publish(
                "error",
                message_key="db_migration_failed",
                code="DATABASE_MIGRATION_FAILED",
                message=db_status.get(
                    "message_zh",
                    "数据库迁移失败，请检查写入权限后运行 python -m openvoicechat.migrate 手动恢复。",
                ),
                message_en=db_status.get(
                    "message_en",
                    "Database migration failed. Please check write permissions and run python -m openvoicechat.migrate to recover.",
                ),
                recovery=db_status.get("recovery", "python -m openvoicechat.migrate"),
                detail=db_status.get("error"),
            )
            publish("state", state="idle")
            return

        try:
            saved_conversation = get_conversation(conversation_id) if conversation_id else None
            if conversation_id and saved_conversation is None:
                publish("error", message_key="history_missing")
                publish("state", state="idle")
                return

            if saved_conversation is not None:
                hist_char_id = saved_conversation.get("character_id")
                hist_voice = saved_conversation.get("voice")
                hist_engine = saved_conversation.get("tts_engine")

                # Check if user explicitly changed character
                if character_id is not None and character_id != hist_char_id:
                    chosen_char_id = character_id
                    char_changed = True
                else:
                    chosen_char_id = hist_char_id
                    char_changed = False

                char = None
                if chosen_char_id:
                    try:
                        char = CharacterConfig.from_file(chosen_char_id)
                    except (OSError, ValueError, TypeError, AttributeError, FileNotFoundError) as exc:
                        log.warning("Failed to load character %s: %s", chosen_char_id, exc)

                if char is None:
                    char = get_default_character()
                    publish("notice", message_key="char_deleted_fallback", name=char.name)
                    chosen_char_id = char.name.lower() or "mio"
                    char_changed = True

                # Check engine
                if (
                    engine_override is not None
                    and hist_engine is not None
                    and engine_override != hist_engine
                ):
                    chosen_engine = engine_override
                    engine_changed = True
                elif hist_engine:
                    chosen_engine = hist_engine
                    engine_changed = False
                else:
                    chosen_engine = engine_override or char.tts_engine or "gpt_sovits"
                    publish("notice", message_key="legacy_settings_fallback")
                    engine_changed = True

                # Check voice
                if (
                    voice_override is not None
                    and hist_voice is not None
                    and voice_override != hist_voice
                ):
                    chosen_voice = voice_override
                    voice_changed = True
                elif hist_voice:
                    chosen_voice = hist_voice
                    voice_changed = False
                else:
                    chosen_voice = voice_override or char.voice or TTS_VOICE
                    if not engine_changed:
                        publish("notice", message_key="legacy_settings_fallback")
                    voice_changed = True

                if char_changed or engine_changed or voice_changed:
                    update_conversation_settings(
                        saved_conversation["id"],
                        character_id=chosen_char_id,
                        character_name=char.name,
                        voice=chosen_voice,
                        tts_engine=chosen_engine,
                    )
            else:
                char = None
                if character_id:
                    try:
                        char = CharacterConfig.from_file(character_id)
                    except (OSError, ValueError, TypeError, AttributeError, FileNotFoundError) as exc:
                        log.warning("Failed to load character %s: %s", character_id, exc)
                if char is None:
                    char = get_default_character()

                chosen_voice = voice_override or char.voice or TTS_VOICE
                chosen_engine = engine_override or char.tts_engine or "gpt_sovits"
                chosen_char_id = character_id or "mio"
                saved_conversation = create_conversation(
                    chosen_char_id,
                    char.name,
                    voice=chosen_voice,
                    tts_engine=chosen_engine,
                )

            session_conversation_id = saved_conversation["id"]
            publish("conversation", id=session_conversation_id)
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            log.error("Failed to access or initialize conversation: %s", exc)
            publish(
                "error",
                message_key="db_migration_failed",
                code="DATABASE_INIT_FAILED",
                message=f"数据库访问或初始化失败：{exc}。请运行 python -m openvoicechat.migrate 手动恢复。",
                message_en=f"Database access or initialization failed: {exc}. Run python -m openvoicechat.migrate to recover.",
                recovery="python -m openvoicechat.migrate",
                detail=str(exc),
            )
            publish("state", state="idle")
            return

        chosen_prompt = char.system_prompt or DEFAULT_PROMPT
        chosen_name = char.name or "Mio"

        publish(
            "character_info",
            name=chosen_name,
            avatar=char.avatar,
            voice=chosen_voice,
            tts_engine=chosen_engine,
        )

        async def run_session():
            nonlocal conversation, output_task
            try:
                async with audio_in_use:
                    publish("state", state="starting")
                    chosen_steps = (
                        steps_override
                        if isinstance(steps_override, int) and steps_override in (4, 8)
                        else websocket.app.state.tts_steps
                    )
                    config, speech = voice_profile(
                        voice=chosen_voice,
                        language="zh",
                        on_partial=lambda text: publish("user_partial", text=text),
                        tts_steps=chosen_steps,
                        tts_engine=chosen_engine,
                    )
                    try:
                        def on_conversation_event(kind: str, **data: Any):
                            record_turn_event(websocket.app, kind, chosen_engine, chosen_steps, **data)
                            publish(kind, **data)

                        conversation = Conversation(
                            speech,
                            config=config,
                            system_prompt=chosen_prompt,
                            history=saved_conversation.get("messages", []),
                            summary=saved_conversation.get("summary"),
                            on_turn=lambda role, text: on_turn(session_conversation_id, role, text),
                            on_summary=lambda text: update_conversation_summary(
                                session_conversation_id, text
                            ),
                            on_event=on_conversation_event,
                            verbose=True,
                            speaker_mode=not barge_in,
                        )
                        output_task = asyncio.create_task(forward_audio_output(conversation))
                        publish("state", state="running")
                        if initial_text:
                            await conversation.send_text(initial_text)
                        elif initial_speak_text:
                            await conversation.retry_speak(initial_speak_text)
                        try:
                            await conversation.run()
                        finally:
                            if output_task is not None:
                                output_task.cancel()
                                await asyncio.gather(output_task, return_exceptions=True)
                                output_task = None
                            await conversation.client.close()
                    finally:
                        await speech.aclose()
            except VoiceConfigurationError as exc:
                publish("error", message=str(exc))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Voice session failed")
                publish("error", message="运行语音对话遇到错误，请刷新后重试。")
            finally:
                publish("state", state="idle")

        voice_task = asyncio.create_task(run_session())

    sender = asyncio.create_task(send_events())
    publish("state", state="idle")
    db_status = ensure_database_ready(websocket.app)
    if db_status.get("status") == "error":
        publish(
            "error",
            message_key="db_migration_failed",
            code="DATABASE_MIGRATION_FAILED",
            message=db_status.get(
                "message_zh",
                "数据库迁移失败，请检查写入权限后运行 python -m openvoicechat.migrate 手动恢复。",
            ),
            message_en=db_status.get(
                "message_en",
                "Database migration failed. Please check write permissions and run python -m openvoicechat.migrate to recover.",
            ),
            recovery=db_status.get("recovery", "python -m openvoicechat.migrate"),
            detail=db_status.get("error"),
        )
    try:
        while True:
            message = await websocket.receive()
            if message.get("bytes"):
                raw = message["bytes"]
                if conversation is not None and not conversation._stop.is_set():
                    input_buffer.extend(raw)
                    while len(input_buffer) >= SPEECH_FRAME_BYTES:
                        frame = bytes(input_buffer[:SPEECH_FRAME_BYTES])
                        del input_buffer[:SPEECH_FRAME_BYTES]
                        await conversation.input.put(frame)
            elif message.get("text"):
                try:
                    command = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue
                if not isinstance(command, dict):
                    continue
                action = command.get("action")
                if action == "init" or action == "characters":
                    chars = list_characters()
                    cur_char = get_default_character()
                    publish("characters", characters=chars, current=cur_char.name)
                if action == "devices":
                    try:
                        publish("devices", **audio_devices(refresh=not audio_in_use.locked()))
                    except Exception:
                        log.exception("Could not list local audio devices")
                        publish("devices_error", message="无法识别音频设备，请检查设备连接后刷新。")
                    chars = list_characters()
                    cur_char = get_default_character()
                    publish("characters", characters=chars, current=cur_char.name)
                elif action == "start" and (voice_task is None or voice_task.done()):
                    mode = command.get("mode")
                    if mode == "speaker":
                        barge_in = False
                    elif mode == "barge_in":
                        barge_in = True
                    else:
                        barge_in = command.get("barge_in", False)
                    char_id = command.get("character")
                    v_override = command.get("voice")
                    e_override = command.get("tts_engine")
                    s_override = command.get("tts_steps")
                    if not isinstance(s_override, int) or s_override not in (4, 8):
                        s_override = None
                    selected_conversation = command.get("conversation_id")
                    if not isinstance(selected_conversation, str):
                        selected_conversation = None
                    initial_text = command.get("text")
                    if not isinstance(initial_text, str) or not initial_text.strip():
                        initial_text = None
                    else:
                        initial_text = initial_text.strip()
                    await start_voice(
                        barge_in,
                        character_id=char_id,
                        voice_override=v_override,
                        engine_override=e_override,
                        steps_override=s_override,
                        conversation_id=selected_conversation,
                        initial_text=initial_text,
                    )
                elif action == "text":
                    text_content = command.get("text")
                    if isinstance(text_content, str) and text_content.strip():
                        text_content = text_content.strip()
                        if conversation is not None and not conversation._stop.is_set():
                            await conversation.send_text(text_content)
                        elif voice_task is None or voice_task.done():
                            mode = command.get("mode")
                            barge_in = (
                                mode == "barge_in"
                                if mode in ("speaker", "barge_in")
                                else command.get("barge_in", False)
                            )
                            char_id = command.get("character")
                            v_override = command.get("voice")
                            e_override = command.get("tts_engine")
                            s_override = command.get("tts_steps")
                            if not isinstance(s_override, int) or s_override not in (4, 8):
                                s_override = None
                            selected_conversation = command.get("conversation_id")
                            if not isinstance(selected_conversation, str):
                                selected_conversation = None
                            await start_voice(
                                barge_in,
                                character_id=char_id,
                                voice_override=v_override,
                                engine_override=e_override,
                                steps_override=s_override,
                                conversation_id=selected_conversation,
                                initial_text=text_content,
                            )
                elif action == "retry_speak":
                    text_content = command.get("text")
                    if isinstance(text_content, str) and text_content.strip():
                        text_content = text_content.strip()
                    else:
                        text_content = None
                    if conversation is not None and not conversation._stop.is_set():
                        await conversation.retry_speak(text_content)
                    elif voice_task is None or voice_task.done():
                        mode = command.get("mode")
                        barge_in = mode == "barge_in" if mode in ("speaker", "barge_in") else command.get("barge_in", False)
                        char_id = command.get("character")
                        v_override = command.get("voice")
                        e_override = command.get("tts_engine")
                        s_override = command.get("tts_steps")
                        if not isinstance(s_override, int) or s_override not in (4, 8):
                            s_override = None
                        selected_conversation = command.get("conversation_id")
                        if not isinstance(selected_conversation, str):
                            selected_conversation = None
                        await start_voice(
                            barge_in,
                            character_id=char_id,
                            voice_override=v_override,
                            engine_override=e_override,
                            steps_override=s_override,
                            conversation_id=selected_conversation,
                            initial_speak_text=text_content,
                        )
                elif action == "playback_started":
                    if conversation is not None:
                        conversation.agent_speaking = True
                    turn_id = command.get("turn_id")
                    play_ms = command.get("playback_latency_ms")
                    net_ms = command.get("network_queue_ms")
                    metrics = getattr(websocket.app.state, "turn_metrics", [])
                    rec = next((r for r in reversed(metrics) if r.get("turn_id") == turn_id), None)
                    if rec is None and metrics:
                        rec = metrics[-1]
                    if rec:
                        if play_ms is not None:
                            rec["playback_latency_ms"] = float(play_ms)
                        if net_ms is not None:
                            rec["network_queue_ms"] = float(net_ms)
                        if rec.get("status") == "pending":
                            rec["status"] = "completed"
                elif action == "interrupted_metric":
                    turn_id = command.get("turn_id")
                    int_ms = command.get("interruption_latency_ms")
                    metrics = getattr(websocket.app.state, "turn_metrics", [])
                    rec = next((r for r in reversed(metrics) if r.get("turn_id") == turn_id), None)
                    if rec is None and metrics:
                        rec = metrics[-1]
                    if rec:
                        rec["status"] = "interrupted"
                        if int_ms is not None:
                            rec["interruption_latency_ms"] = float(int_ms)
                elif action == "playback_ended":
                    if conversation is not None:
                        conversation.notify_playback_ended()
                elif action == "stop":
                    await stop_voice()
                    publish("state", state="idle")
    except (WebSocketDisconnect, WebSocketDisconnected):
        pass
    finally:
        sender.cancel()
        await stop_voice()
        await asyncio.gather(sender, return_exceptions=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=PORT, help="port on 127.0.0.1")
    parser.add_argument(
        "--tts-steps",
        type=int,
        choices=(4, 8),
        default=TTS_SAMPLE_STEPS,
        help="GPT-SoVITS v4 sampling steps",
    )
    args = parser.parse_args()
    app.state.tts_steps = args.tts_steps
    uvicorn.run(app, host="127.0.0.1", port=args.port)
