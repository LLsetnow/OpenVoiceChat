# OpenVoiceChat

**The local voice runtime for Python.** Parakeet STT, Kokoro TTS, Silero VAD —
one GPU, many calls, no cloud voice APIs, no provider zoo, no platform.

> Status: **0.4.0, alpha.** The local pipeline, mic loop, and `ovc doctor` work.
> The shared multi-call runtime, Twilio/Daily transports, and `ovc init/run/bench`
> are the next steps. See [Roadmap](#roadmap).

## What it is

A small library for embedding an interruptible voice agent in a Python process:

- **STT:** NVIDIA Parakeet TDT 0.6B v3 via `onnx-asr` (ONNX Runtime)
- **TTS:** Kokoro-82M
- **VAD / endpointing:** Silero VAD v5
- **LLM:** any OpenAI-compatible API; `Config(llm_base_url=..., llm_model=...)`
  points at OpenAI, Ollama, llama.cpp, vLLM, or your own gateway

It is not a platform, a model zoo, or a cloud speech wrapper. The local web
interface includes SQLite-backed conversation history with resume support,
and a fully internationalized UI (English / Chinese) with automatic locale
detection and instant switching. There is no hosted dashboard, auth service,
or provider registry.

## Architecture

```text
microphone ──► resample to 16 kHz ──► Conversation.input
                                           │
                                    Silero VAD + endpointing
                                           │ utterance PCM16
                                           ▼
                                   Parakeet (local STT)
                                           │ text
                                           ▼
                              OpenAI-compatible LLM (stream)
                                           │ sentences
                                           ▼
                                    Kokoro (local TTS)
                                           │ 16 kHz PCM16
                                           ▼
speaker ◄── resample to device rate ◄── Conversation.output
```

`Conversation` is the whole engine: endpointing, barge-in, sentence-level TTS,
and history. Transports only move PCM16 in and out.

## Quickstart

Python 3.11+ is required.

```bash
pip install "openvoicechat[local,models]"
cp .env.example .env      # then set LLM_API_KEY
```

```python
# voice.py — talk through the microphone and speakers
import asyncio

from openvoicechat import Config, Conversation
from openvoicechat.audio import run_local
from openvoicechat.local_speech import LocalSpeech


async def main() -> None:
    speech = LocalSpeech()
    conversation = Conversation(
        speech,
        config=Config(),
        on_turn=lambda role, text: print(f"{role.upper()}: {text}"),
    )
    await run_local(conversation)


asyncio.run(main())
```

```bash
python voice.py
```

From a checkout, `python main.py --verbose` runs the same loop and exposes
`--model`, `--base-url`, `--voice`, and device flags. The `ovc` CLI currently
provides `ovc doctor`; `init`, `run`, `bench`, and `serve` land next.

The first run downloads Parakeet (~2 GB), Kokoro-82M (~330 MB), and Silero VAD
(~2 MB). `--verbose` prints speech start/end, endpointing, STT, LLM first-token,
and TTS timings. `Ctrl-C` stops it.

On Linux you may need PortAudio: `sudo apt install libportaudio2`.

`--prompt`, `--input-device`, and `--output-device` are available. Audio is
resampled between the device rate and 16 kHz internally. **Use headphones** —
there is no echo cancellation, and speaker output will feed back into the VAD.

## Mac 语音对话：DeepSeek、阿里云与 GPT-SoVITS

这个入口使用 Mac 的麦克风和扬声器。Silero VAD 在本机检测说话，阿里云
`paraformer-realtime-v2` 在说话期间通过 WebSocket 接收音频。停顿后，识别结果交给
DeepSeek V4.1 Flash（`deepseek-flash`，关闭思考模式）。生成的回复按句送到
AiServer 的 GPT-SoVITS v4 接口，默认用千夏（`chinatsu`）。建议戴耳机，避免喇叭声音触发麦克风。

在项目目录安装并启动：

```bash
uv venv --python 3.12
uv pip install -e '.[aliyun]'
.venv/bin/python run_voice_chat.py -v
```

启动终端需有 `DEEPSEEK_API_KEY` 和 `ALIYUN_API_KEY`。也可以填在项目目录的 `.env`
中，格式见 `.env.example`。阿里云密钥需有北京地域的 Paraformer 访问权限。
启动前在 AiServer 确认 GPT-SoVITS v4 已运行，Mac 转发地址为 `http://127.0.0.1:19889/`。
首次启动加载 Silero VAD，macOS 提示时需要允许终端使用麦克风。

可以用 `--voice` 换音色，用 `--tts-language en` 练习纯英文对话，或用 `--prompt`
指定对话主题。合成语言默认为中文混读模式（`zh`）。按 `Ctrl+C` 结束对话。
回复首段在自然的短分句处开始合成，后续按整句合成。GPT-SoVITS 默认使用 8 步，
命令行和网页服务均可用 `--tts-steps 4` 改为 4 步。

### 本地网页

```bash
uv pip install -e '.[aliyun,web]'
.venv/bin/python -m openvoicechat.migrate
.venv/bin/python run_voice_web.py
```

打开 `http://127.0.0.1:18768`，选好麦克风和声音输出后点击「开始对话」。网页使用
同一组环境变量，默认跟随 Mac 系统音频设置。接入耳机或麦克风后可以点「刷新设备」。
停止对话后即可切换设备。页面会显示实时转写、千夏的文字回复，以及 ASR 判停至最终转写、
LLM 首 token 和首段、TTS 首段的耗时。点击「停止对话」或关闭页面会释放麦克风。

网页默认开启「扬声器模式」。千夏开始播放后暂停识别，等整轮音频播完并留出短暂的回声
消退时间后恢复。看到「可以说话」再开口。戴耳机时可以在开始对话前关闭该模式，以便
在回复播放期间插话。

#### 历史对话记录与继续交流

网页支持在本地完整保存与恢复会话：

- **数据存储与迁移**：对话记录持久化保存在本地 SQLite 数据库（默认路径为 `data/conversations.sqlite3`），已加入 `.gitignore`，仅存储在本机，不会随 Git 提交或上传至公开仓库。Web 服务在启动时会自动检测并应用版本化迁移；亦可随时运行 `.venv/bin/python -m openvoicechat.migrate` 手动初始化或恢复。若历史列表提示数据库迁移失败，可按页面提示运行迁移命令排查后刷新恢复。
- **查看历史**：左侧面板按更新时间倒序列出历史会话卡片。点击任意卡片，右侧即可展开该会话全部的历史交互气泡与时间线。
- **继续交流**：选中历史会话后，点击操作栏的「继续对话」（英文模式为「Resume conversation」），系统会自动将过往对话记录载入当前会话上下文，大模型将基于上文记忆无缝继续交流。
- **新建对话**：点击「新建」（英文模式为「New」）即可清空当前会话上下文，开始一段全新的独立对话。

#### 文本输入与混合交互

网页对话面板底部提供文本输入框与发送按钮，支持打字与语音混合交流：

- **文字发送**：输入框中输入文字后按 `Enter` 键或点击「发送」，即可直接向助手提问。助手将在界面输出字幕的同时进行实时语音合成，通过扬声器播放语音回复。
- **即时打断**：在助手正在播放语音或生成回复时发送文字，系统会自动打断上一轮语音输出，无缝切换并立即响应新发送的内容。
- **免麦开局**：在尚未点击通话时，亦可直接输入文字并发送，系统会自动连接并启动对话，即使在安静环境或无麦克风时也能流畅交互。

#### 全界面中英文国际化

网页提供完整的中英文双语支持：

- **自动适配**：首次打开页面时，前端会自动识别浏览器与系统语言（`navigator.language`）。中文环境默认显示简体中文（`zh-CN`），其他环境默认显示英文（`en`）。亦可在 URL 后面附加 `?lang=zh-CN` 或 `?lang=en` 强制指定显示语言。
- **顶部一键切换**：页面顶部导航栏右侧（状态指示灯旁）提供了语言切换按钮（`文/A English` / `文/A 中文`）。点击即可无刷新秒级切换全站文案，包括页面标题、控制按钮、音色与角色描述、音频设备状态、实时延迟指标、角色称呼（你 ↔ You，千夏 ↔ Chinatsu）以及交互提示语。
- **偏好持久化**：用户手动切换的语言设置会自动保存在浏览器的 `localStorage` 中，后续访问页面将优先保留用户的手动设置。

角色示例在 `characters/example.json`；个人角色文件 `characters/mio.json` 会保留在本机，
不会上传到 public 仓库。

## Models and hardware

| Component | Model | Notes |
|---|---|---|
| STT | `nvidia/parakeet-tdt-0.6b-v3` | ONNX Runtime; CUDA where available, CPU otherwise |
| TTS | `hexgrad/Kokoro-82M` | 24 kHz, resampled to 16 kHz for the pipeline |
| VAD | Silero VAD v5 | Always CPU, ~0.4% of one core |

On Linux x86_64 and Windows AMD64 the core dependency is `onnxruntime-gpu`, so
Parakeet uses CUDA when present and falls back to CPU. macOS and non-x86 Linux
use the CPU `onnxruntime` build. Kokoro uses CUDA when `torch` reports it.

Measured on one RTX 3080 Ti (12 GB), Python 3.12:

| Metric | Value |
|---|---|
| Model load (Parakeet + Kokoro) | ~6.9 s |
| Kokoro warm RTF | ~0.01 |
| Parakeet steady transcription | ~0.019 s per utterance |
| Peak VRAM | ~5.2 GB |

These are single-session numbers. Concurrent-call benchmarks are not published
yet; that measurement is part of the next milestone.

Pick a voice with `--voice` (default `af_heart`, see the
[Kokoro voice list](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md)).

### Startup warnings

Startup prints one ONNX Runtime CUDA memcpy notice and three torch deprecation
warnings from Kokoro. They are expected and do not affect output:

- Kokoro runs in `eval()` mode and its dropout calls pass `training=False`.
- The dropout warning is PyTorch noting an unused `dropout` argument on a
  single-layer LSTM, where dropout is ignored by definition.
- The other two are genuine deprecations inside Kokoro's model code; they will
  disappear when Kokoro updates.

They print once per process. The library does not hide them.

## Configuration

`.env` holds secrets only:

```dotenv
LLM_API_KEY=...
AGENT_API_TOKEN=...
DAILY_API_KEY=...
```

Everything else is a field on `Config` in `openvoicechat/config.py`:

```python
from openvoicechat import Config

config = Config(
    llm_model="gpt-6-luna",
    llm_base_url="",           # empty means https://api.openai.com/v1
    endpoint_silence_ms=400,
    vad_threshold=0.6,
)
```

`main.py` exposes `--model`, `--base-url`, and `--voice`; edit `Config` for
everything else. Nothing besides secrets is read from the environment.

## LLM

The default is OpenAI with `gpt-6-luna`. Point it elsewhere with flags:

```bash
python main.py --base-url http://localhost:11434/v1 --model qwen3:4b
```

or in code:

```python
config = Config(llm_base_url="http://localhost:8000/v1", llm_model="my-model")
```

llama.cpp (`http://localhost:8080/v1`) and vLLM (`http://localhost:8000/v1`)
work the same way. `llm_timeout_s` defaults to 15 seconds with one retry, so a
stalled model cannot freeze a turn.

## Check your machine

```bash
ovc doctor
ovc doctor --json
```

`doctor` checks Python, `torch`/CUDA, ONNX Runtime providers, Parakeet and Kokoro
imports, audio devices, the Silero cache, and LLM configuration. It exits `0`
when ready and `2` when something required is missing. `--json` emits a stable
schema for agents:

```json
{
  "schema_version": 1,
  "ok": true,
  "checks": [{"name": "onnxruntime", "status": "ok", "detail": "1.31.1 | CUDAExecutionProvider"}]
}
```

## Turn taking

| Config field | Default | Effect |
|---|---:|---|
| `endpoint_silence_ms` | `600` | Silence before an utterance is sent to STT |
| `pre_roll_ms` | `300` | Audio kept before speech onset |
| `vad_threshold` | `0.6` | Silero speech probability threshold |
| `min_speech_ms` | `250` | Utterances shorter than this are dropped |
| `max_utterance_ms` | `30000` | Rolling window for one uninterrupted utterance |
| `output_chunk_ms` | `100` | TTS chunk size written to the transport |

`max_utterance_ms` does not cut a speaker off mid-sentence. Past the cap the
engine keeps the most recent window and waits for silence, so a long monologue
costs bounded memory instead of cancelling its own turn.

Override any of these in code, for example `Config(endpoint_silence_ms=400)`.

## Daily (optional)

The Daily transport lives in `openvoicechat/daily_agent/`. It is a thin call client,
but it still starts **one process per meeting**, so each meeting loads its own
models. Moving it onto a shared runtime is the next milestone.

```bash
pip install -e ".[daily,models]"
python scripts/daily_test.py --open
```

The script creates a room, starts the agent server, joins the agent, tails the
transcript, and cleans up on `Ctrl-C`.

Server:

```bash
uvicorn openvoicechat.daily_agent.server:app --host 0.0.0.0 --port 8001
```

`GET /health` is public; all other routes require
`Authorization: Bearer $AGENT_API_TOKEN`. `POST /join_meeting` takes
`meeting_url`, `prompt`, and `conversation_id`.

## Tests

```bash
pytest -q
ruff check .
```

Tests use fake providers and no network or GPU.

## Roadmap

| Milestone | Content |
|---|---|
| **0.4** | Local-only core, `ovc doctor`, P1 turn-taking fixes |
| **0.5** | `VoiceRuntime`: load models once, serve many sessions; `ovc init/run/bench` |
| **0.6** | Twilio Media Streams transport and scaffold |
| **0.7** | Shared-runtime Daily transport, `ovc eval`, concurrency benchmarks |
| **1.0** | Stable API, published comparisons, extension docs |

Full browser STT + TTS is a separate later package, not part of the Python core.

## License and attribution

Code is Apache-2.0. Model licenses are upstream: Parakeet is CC-BY-4.0, Kokoro is
Apache-2.0, and Silero VAD is MIT. Check each model card before redistribution.
