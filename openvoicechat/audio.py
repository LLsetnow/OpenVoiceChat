"""Local microphone/speaker I/O and PCM16 resampling."""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .conversation import Conversation

SPEECH_RATE = 16000
FRAME_SAMPLES = 512
FRAME_SECONDS = FRAME_SAMPLES / SPEECH_RATE
SPEECH_FRAME_BYTES = FRAME_SAMPLES * 2
SPEAKER_ECHO_TAIL_SECONDS = 0.25


def audio_devices(*, refresh: bool = False) -> dict:
    """List selectable devices and the current system defaults by direction."""
    import sounddevice as sd

    if refresh and sys.platform == "darwin":
        # Core Audio's PortAudio device list is built at initialization.
        # Callers refresh only when all streams are closed.
        if sd._initialized:
            sd._terminate()
        sd._initialize()

    devices = sd.query_devices()
    host_apis = sd.query_hostapis()
    defaults = dict(zip(("input", "output"), sd.default.device))
    result = {}
    for kind, default in defaults.items():
        entries = []
        names: Counter = Counter()
        for device in devices:
            if not device[f"max_{kind}_channels"]:
                continue
            name = device["name"]
            host = host_apis[device["hostapi"]]["name"]
            names[host, name] += 1
            occurrence = names[host, name]
            entries.append({
                "id": f"{host}:{name}:{occurrence}",
                "label": name if occurrence == 1 else f"{name} ({occurrence})",
                "index": device["index"],
            })
        default_label = next((item["label"] for item in entries if item["index"] == default), None)
        result[kind] = {"default": default_label, "devices": entries}
    return result


class Pcm16Resampler:
    """Stateful PCM16 resampler; avoids clicks at chunk boundaries."""

    def __init__(self, source: int, target: int):
        self._identity = source == target
        if not self._identity:
            import soxr

            self._stream = soxr.ResampleStream(
                source, target, 1, dtype="int16", quality="HQ"
            )

    def process(self, pcm: bytes) -> bytes:
        if not pcm or self._identity:
            return pcm
        data = np.frombuffer(pcm, dtype=np.int16)
        return self._stream.resample_chunk(data, last=False).tobytes()

    def finish(self) -> bytes:
        if self._identity:
            return b""
        return self._stream.resample_chunk(np.empty(0, dtype=np.int16), last=True).tobytes()


def _default_rate(sounddevice, kind: str, device=None) -> int:
    try:
        return round(
            float(sounddevice.query_devices(device, kind=kind)["default_samplerate"])
        )
    except Exception:  # noqa: BLE001 - PortAudio raises its own exception types
        return 48000


async def microphone(conversation: Conversation, stream, rate: int) -> None:
    """Read device audio, resample to 16 kHz, and feed conversation.input."""
    frames = max(1, int(rate * FRAME_SECONDS))
    resampler = Pcm16Resampler(rate, SPEECH_RATE)
    buffer = bytearray()
    while True:
        data, overflowed = await asyncio.to_thread(stream.read, frames)
        if overflowed:
            print("input overflow", file=sys.stderr, flush=True)
        if not data:
            await asyncio.sleep(0.01)
            continue
        buffer.extend(resampler.process(bytes(data)))
        while len(buffer) >= SPEECH_FRAME_BYTES:
            chunk = bytes(buffer[:SPEECH_FRAME_BYTES])
            del buffer[:SPEECH_FRAME_BYTES]
            await conversation.input.put(chunk)


async def speaker(conversation: Conversation, stream, rate: int, *, input_latency: float = 0) -> None:
    """Drain conversation.output and write it at the device rate."""
    resampler = Pcm16Resampler(SPEECH_RATE, rate)
    while True:
        pcm = await conversation.output.get()
        if pcm is None:
            if tail := resampler.finish():
                await asyncio.to_thread(stream.write, tail)
            resampler = Pcm16Resampler(SPEECH_RATE, rate)
            # Blocking writes hand PCM to PortAudio, which still has buffered playback.
            await asyncio.sleep(stream.latency + input_latency + SPEAKER_ECHO_TAIL_SECONDS)
            conversation.resume_input()
            continue
        data = resampler.process(pcm)
        if data:
            await asyncio.to_thread(stream.write, data)


async def run_local(
    conversation: Conversation,
    *,
    input_device=None,
    output_device=None,
    on_ready=None,
) -> None:
    """Run one conversation against the chosen microphone and speaker."""
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise ImportError(
            'Missing audio dependencies. Install with: pip install -e ".[local]"'
        ) from exc

    input_rate = _default_rate(sd, "input", input_device)
    output_rate = _default_rate(sd, "output", output_device)
    print(
        f"Audio: mic {input_rate} Hz, speaker {output_rate} Hz, speech {SPEECH_RATE} Hz",
        flush=True,
    )
    with sd.RawInputStream(
        samplerate=input_rate,
        channels=1,
        dtype="int16",
        blocksize=0,
        device=input_device,
    ) as input_stream, sd.RawOutputStream(
        samplerate=output_rate,
        channels=1,
        dtype="int16",
        blocksize=0,
        device=output_device,
    ) as output_stream:
        print("Listening. Ctrl-C to stop.", flush=True)
        tasks = [
            asyncio.create_task(conversation.run(), name="conversation"),
            asyncio.create_task(
                microphone(conversation, input_stream, input_rate), name="microphone"
            ),
            asyncio.create_task(
                speaker(conversation, output_stream, output_rate, input_latency=input_stream.latency),
                name="speaker",
            ),
        ]
        try:
            if on_ready is not None:
                on_ready()
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.cancelled():
                    continue
                if task.exception() is not None:
                    raise RuntimeError(f"{task.get_name()} failed: {task.exception()}") from task.exception()
        finally:
            conversation.stop()
            input_stream.abort()
            output_stream.abort()
            for task in tasks[1:]:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
