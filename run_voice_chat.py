"""Mac microphone chat using DeepSeek, Aliyun Paraformer and AiServer GPT-SoVITS."""

import argparse
import asyncio
import json
import os

from main import _device
from openvoicechat.aliyun_sovits import (
    ASR_MODEL,
    TTS_SAMPLE_STEPS,
    TTS_VOICE,
    AliyunSpeechPipeline,
)
from openvoicechat.audio import run_local
from openvoicechat.character import CharacterConfig, get_default_character
from openvoicechat.config import Config
from openvoicechat.conversation import Conversation

MODEL = "deepseek-flash"
DEFAULT_PROMPT = (
    "你是一个自然、简洁的语音助手。用用户当前使用的语言回答。"
    "开头直接给出具体答案或行动建议，省去开场和复述。"
    "第一分句约七到十四个中文字或三到八个英文词，紧接自然的逗号或句号。"
    "优先用一两句回答。用户要求一句话时只用一句。避免 Markdown、表格和链接。"
)


class VoiceConfigurationError(ValueError):
    pass


def voice_profile(
    *, voice: str, language: str, on_partial=None, tts_steps: int = TTS_SAMPLE_STEPS, tts_engine: str = "gpt_sovits",
) -> tuple[Config, AliyunSpeechPipeline]:
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    aliyun_key = os.getenv("ALIYUN_API_KEY", "").strip()
    missing = [
        name for name, value in (("DEEPSEEK_API_KEY", deepseek_key), ("ALIYUN_API_KEY", aliyun_key))
        if not value
    ]
    if missing:
        raise VoiceConfigurationError(
            f"缺少环境变量：{', '.join(missing)}。请在启动终端中导出，或设在项目的 .env 文件中。"
        )

    config = Config(
        llm_api_key=deepseek_key,
        llm_base_url="https://api.deepseek.com",
        llm_model=MODEL,
        llm_extra_body={"thinking": {"type": "disabled"}},
        endpoint_silence_ms=400,
        speak_first_clause=True,
    )
    return config, AliyunSpeechPipeline(
        aliyun_key, voice=voice, language=language, on_partial=on_partial, sample_steps=tts_steps, tts_engine=tts_engine,
    )


async def run(args: argparse.Namespace) -> None:
    char = None
    if args.character:
        try:
            char = CharacterConfig.from_file(args.character)
        except (OSError, json.JSONDecodeError, TypeError, AttributeError) as exc:
            raise SystemExit(f"加载人物设定失败: {exc}") from None
    elif not args.prompt_set:
        char = get_default_character()

    prompt = args.prompt
    voice = args.voice
    tts_engine = args.tts_engine
    if char:
        prompt = char.system_prompt or prompt
        if not args.voice_set and char.voice:
            voice = char.voice
        if not args.engine_set and char.tts_engine:
            tts_engine = char.tts_engine
        print(f"Loaded character: {char.name} (voice: {voice}, engine: {tts_engine})", flush=True)

    try:
        config, speech = voice_profile(
            voice=voice, language=args.tts_language, tts_steps=args.tts_steps, tts_engine=tts_engine,
        )
    except VoiceConfigurationError as exc:
        raise SystemExit(str(exc)) from None
    try:
        print(f"LLM {MODEL} | ASR {ASR_MODEL} | TTS {voice} ({tts_engine})", flush=True)
        conversation = Conversation(
            speech,
            system_prompt=prompt,
            config=config,
            on_turn=lambda role, text: print(f"{role.upper()}: {text}", flush=True),
            verbose=args.verbose,
        )
        try:
            await run_local(
                conversation, input_device=args.input_device, output_device=args.output_device
            )
        finally:
            await conversation.client.close()
    finally:
        await speech.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--character", "-c", help="character JSON filename or path (e.g. mio)")
    parser.add_argument("--voice", default=TTS_VOICE, help="TTS voice ID / name (e.g. chinatsu, ling, zhe, etc.)")
    parser.add_argument(
        "--tts-engine", default="gpt_sovits", choices=("gpt_sovits", "cosyvoice"),
        help="TTS engine (gpt_sovits or cosyvoice)",
    )
    parser.add_argument(
        "--tts-steps", type=int, choices=(4, 8), default=TTS_SAMPLE_STEPS,
        help="GPT-SoVITS v4 sampling steps",
    )
    parser.add_argument(
        "--tts-language", default="zh", choices=("zh", "en", "auto", "all_ja", "all_ko"),
        help="GPT-SoVITS text language (default: zh, supports mixed English)",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="system prompt")
    parser.add_argument("--input-device", type=_device, help="sounddevice input device")
    parser.add_argument("--output-device", type=_device, help="sounddevice output device")
    parser.add_argument("-v", "--verbose", action="store_true", help="show timing for each turn")
    args = parser.parse_args()
    args.voice_set = any(arg.startswith("--voice") for arg in os.sys.argv[1:])
    args.engine_set = any(arg.startswith("--tts-engine") for arg in os.sys.argv[1:])
    args.prompt_set = any(arg.startswith("--prompt") for arg in os.sys.argv[1:])
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)


if __name__ == "__main__":
    main()
