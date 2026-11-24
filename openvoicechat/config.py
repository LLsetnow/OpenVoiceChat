"""Configuration.

Only secrets are read from the environment (or a local ``.env``). Everything
else is a plain dataclass field, set in code or overridden per instance:

    Config(endpoint_silence_ms=400, llm_model="qwen3:4b")
"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv(".env")

DEFAULT_LLM_BASE_URL = ""  # empty means https://api.openai.com/v1
DEFAULT_LLM_MODEL = "gpt-6-luna"
DEFAULT_SOVITS_URL = "http://127.0.0.1:19889"


@dataclass(frozen=True)
class Config:
    # Secrets: environment only.
    llm_api_key: str = field(
        default_factory=lambda: os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", "")
    )
    agent_api_token: str = field(default_factory=lambda: os.getenv("AGENT_API_TOKEN", ""))

    # LLM endpoint.
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_model: str = DEFAULT_LLM_MODEL
    llm_timeout_s: float = 15.0
    context_token_budget: int = 2048
    summary_token_budget: int = 512

    # Voice services.
    sovits_url: str = field(
        default_factory=lambda: os.getenv("SOVITS_URL") or DEFAULT_SOVITS_URL
    )

    # Runtime and turn taking.
    max_sessions: int = 5
    data_dir: str = "data"
    sample_rate: int = 16000
    frame_samples: int = 512
    output_chunk_ms: int = 100
    vad_threshold: float = 0.6
    endpoint_silence_ms: int = 600
    pre_roll_ms: int = 300
    min_speech_ms: int = 250
    max_utterance_ms: int = 30000
    llm_extra_body: dict[str, object] | None = None
    speak_first_clause: bool = False

    def __post_init__(self) -> None:
        if self.sovits_url:
            object.__setattr__(self, "sovits_url", self.sovits_url.rstrip("/"))
