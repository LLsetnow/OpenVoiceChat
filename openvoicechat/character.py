"""Character persona configuration loader."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

CHARACTERS_DIR = Path(__file__).resolve().parent.parent / "characters"
log = logging.getLogger(__name__)


@dataclass
class CharacterConfig:
    name: str = "Mio"
    avatar: str = "/assets/img/chinatsu.png"
    system_prompt: str = ""
    voice: str = "chinatsu"
    tts_engine: str = "gpt_sovits"
    file_path: str = ""

    @classmethod
    def from_file(cls, path_or_name: str | Path) -> CharacterConfig:
        path = Path(path_or_name)
        if not path.is_file():
            candidate = CHARACTERS_DIR / f"{path_or_name}.json"
            if candidate.is_file():
                path = candidate
            elif (CHARACTERS_DIR / path_or_name).is_file():
                path = CHARACTERS_DIR / path_or_name
            else:
                raise FileNotFoundError(f"Character config not found: {path_or_name}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return cls(
            name=data.get("name", "Mio"),
            avatar=data.get("avatar", "/assets/img/chinatsu.png"),
            system_prompt=data.get("system_prompt", ""),
            voice=data.get("voice", "chinatsu"),
            tts_engine=data.get("tts_engine", "gpt_sovits"),
            file_path=str(path),
        )


def list_characters() -> list[dict]:
    """List all available character profiles in characters/ directory."""
    if not CHARACTERS_DIR.is_dir():
        return []
    results = []
    for p in sorted(CHARACTERS_DIR.glob("*.json")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            results.append({
                "id": p.stem,
                "name": data.get("name", p.stem),
                "avatar": data.get("avatar", ""),
                "voice": data.get("voice", "chinatsu"),
                "tts_engine": data.get("tts_engine", "gpt_sovits"),
                "system_prompt": data.get("system_prompt", ""),
                "file": p.name,
            })
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            log.warning("Skipping invalid character profile %s: %s", p.name, exc)
            continue
    return results


def get_default_character() -> CharacterConfig:
    """Get default character (characters/mio.json or first available)."""
    mio_path = CHARACTERS_DIR / "mio.json"
    if mio_path.is_file():
        return CharacterConfig.from_file(mio_path)
    chars = list_characters()
    if chars:
        return CharacterConfig.from_file(chars[0]["id"])
    return CharacterConfig()
