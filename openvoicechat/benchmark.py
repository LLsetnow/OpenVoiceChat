"""Benchmark suite and latency statistics for OpenVoiceChat.

Supports measuring end-to-end playback latency, interruption latency,
component-level breakdowns (ASR, LLM first-token, LLM sentence end, TTS first-chunk),
p50/p95 percentile aggregations, and GPT-SoVITS 4-step vs 8-step comparisons.
"""

import argparse
import asyncio
import dataclasses
import json
import math
import platform
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

# Default fixed benchmark prompts representing typical interactive voice turns
DEFAULT_BENCHMARK_PROMPTS = [
    "你好，请问今天天气怎么样？",
    "你能用一句话介绍一下你自己吗？",
    "推荐三首好听的纯音乐或轻音乐。",
    "如果我想学习编程，应该从哪门语言开始？",
    "给我讲一个十个字以内的冷笑话。",
    "用简短的一句话解释什么是量子力学。",
    "明天的会议几点开始？请帮我确认一下。",
    "请用一两句话总结人工智能的未来发展趋势。",
]


def calculate_percentile(data: Sequence[float], percentile: float) -> float:
    """Calculate the p-th percentile of a sequence of numbers using linear interpolation."""
    if not data:
        return 0.0
    sorted_data = sorted(float(x) for x in data)
    if percentile <= 0:
        return sorted_data[0]
    if percentile >= 100:
        return sorted_data[-1]

    index = (len(sorted_data) - 1) * (percentile / 100.0)
    floor_idx = math.floor(index)
    ceil_idx = math.ceil(index)
    if floor_idx == ceil_idx:
        return sorted_data[int(index)]
    weight = index - floor_idx
    return sorted_data[floor_idx] * (1.0 - weight) + sorted_data[ceil_idx] * weight


def calculate_statistics(latencies: Sequence[float]) -> dict[str, float | int]:
    """Compute summary statistics: count, min, max, avg, p50, p95.

    Filters out non-finite or negative values.
    """
    valid = [float(x) for x in latencies if math.isfinite(x) and x >= 0]
    if not valid:
        return {
            "count": 0,
            "min": 0.0,
            "max": 0.0,
            "avg": 0.0,
            "p50": 0.0,
            "p95": 0.0,
        }
    return {
        "count": len(valid),
        "min": round(min(valid), 2),
        "max": round(max(valid), 2),
        "avg": round(sum(valid) / len(valid), 2),
        "p50": round(calculate_percentile(valid, 50), 2),
        "p95": round(calculate_percentile(valid, 95), 2),
    }


def collect_environment_info() -> dict[str, Any]:
    """Collect system, hardware, and runtime environment details for reproducible benchmarking."""
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "timestamp": datetime.now(UTC).isoformat(),
    }


@dataclasses.dataclass
class BenchmarkTurn:
    """Record of a single benchmark turn."""
    sample_id: int
    prompt: str
    status: str  # "completed", "cancelled", "failed"
    error: str | None = None
    asr_ms: float = 0.0
    llm_first_token_ms: float | None = None
    llm_end_ms: float | None = None
    tts_first_chunk_ms: float | None = None
    tts_total_ms: float | None = None
    network_queue_ms: float | None = None
    playback_latency_ms: float | None = None
    interruption_latency_ms: float | None = None
    total_turn_ms: float | None = None
    tts_engine: str = "gpt_sovits"
    tts_steps: int = 8

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class BenchmarkReport:
    """Aggregated benchmark report with environment metadata and metric summaries."""
    environment: dict[str, Any]
    config: dict[str, Any]
    turns: list[BenchmarkTurn]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "config": self.config,
            "summary": self.summary,
            "turns": [turn.to_dict() for turn in self.turns],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def format_markdown(self) -> str:
        """Format the report into clean GitHub Markdown tables."""
        lines = [
            f"# OpenVoiceChat 延迟基准评测报告 ({self.config.get('tts_engine', 'tts')})",
            "",
            f"- **测试时间**: `{self.environment.get('timestamp')}`",
            f"- **系统环境**: `{self.environment.get('platform')}` ({self.environment.get('machine')})",
            f"- **Python 版本**: `{self.environment.get('python_version')}`",
            f"- **TTS 引擎与步数**: `{self.config.get('tts_engine')}` · `{self.config.get('tts_steps')} 步`",
            f"- **总轮次**: {self.summary.get('total_turns', 0)} (成功: {self.summary.get('completed_turns', 0)}, 取消: {self.summary.get('cancelled_turns', 0)}, 失败: {self.summary.get('failed_turns', 0)})",
            "",
            "## 核心延迟指标统计 (ms)",
            "",
            "| 阶段 / 指标 | 样本数 | Min | Max | Avg | P50 (中位数) | P95 |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
        ]
        metrics_map = {
            "llm_first_token_ms": "LLM 首字延迟",
            "llm_end_ms": "LLM 首句产出",
            "tts_first_chunk_ms": "TTS 首包合成",
            "playback_latency_ms": "首段播放 (端到端发声)",
            "interruption_latency_ms": "插话停止延迟",
            "total_turn_ms": "单轮总耗时",
        }
        metrics_data = self.summary.get("metrics", {})
        for key, name in metrics_map.items():
            stat = metrics_data.get(key, {})
            if stat.get("count", 0) > 0:
                lines.append(
                    f"| {name} | {stat.get('count')} | {stat.get('min')} | {stat.get('max')} | "
                    f"{stat.get('avg')} | **{stat.get('p50')}** | **{stat.get('p95')}** |"
                )
        lines.extend([
            "",
            "## 逐轮样本记录",
            "",
            "| ID | 测试样本 | 状态 | LLM 首字 | TTS 首包 | 首段播放 | 插话停止 | 总耗时 |",
            "| :-: | :--- | :-: | :-: | :-: | :-: | :-: | :-: |",
        ])
        for t in self.turns:
            status_mark = "✅ 成功" if t.status == "completed" else f"❌ {t.status}"
            llm_f = f"{t.llm_first_token_ms}ms" if t.llm_first_token_ms is not None else "—"
            tts_f = f"{t.tts_first_chunk_ms}ms" if t.tts_first_chunk_ms is not None else "—"
            play = f"**{t.playback_latency_ms}ms**" if t.playback_latency_ms is not None else "—"
            stop = f"{t.interruption_latency_ms}ms" if t.interruption_latency_ms is not None else "—"
            tot = f"{t.total_turn_ms}ms" if t.total_turn_ms is not None else "—"
            lines.append(f"| {t.sample_id} | {t.prompt[:25]}... | {status_mark} | {llm_f} | {tts_f} | {play} | {stop} | {tot} |")

        return "\n".join(lines)


class BenchmarkRunner:
    """Executes latency benchmark suites with real or mock pipelines."""

    def __init__(
        self,
        *,
        mock: bool = False,
        tts_engine: str = "gpt_sovits",
        tts_steps: int = 8,
        voice: str = "chinatsu",
        llm_model: str = "deepseek-flash",
        temperature: float = 0.7,
        top_p: float = 0.95,
    ):
        self.mock = mock
        self.tts_engine = tts_engine
        self.tts_steps = tts_steps
        self.voice = voice
        self.llm_model = llm_model
        self.temperature = temperature
        self.top_p = top_p

    async def run_turn(
        self,
        prompt: str,
        sample_id: int,
        *,
        tts_steps: int | None = None,
        simulate_interrupt: bool = False,
        simulate_cancel: bool = False,
        simulate_failure: bool = False,
    ) -> BenchmarkTurn:
        """Execute a single benchmark turn."""
        steps = tts_steps or self.tts_steps
        turn = BenchmarkTurn(
            sample_id=sample_id,
            prompt=prompt,
            status="completed",
            tts_engine=self.tts_engine,
            tts_steps=steps,
        )

        if simulate_cancel:
            turn.status = "cancelled"
            turn.error = "Turn cancelled by client"
            return turn
        if simulate_failure:
            turn.status = "failed"
            turn.error = "Simulated upstream service error"
            return turn

        start_time = time.monotonic()

        if self.mock:
            # Realistic deterministic mock latencies for benchmarking & automated testing
            # 8-step baseline vs 4-step low-latency
            base_llm_first = 280.0 + (len(prompt) % 5) * 15.0
            base_llm_end = base_llm_first + 220.0 + (len(prompt) % 4) * 20.0
            if steps == 4:
                base_tts_first = 420.0 + (len(prompt) % 3) * 18.0
            else:
                base_tts_first = 760.0 + (len(prompt) % 3) * 28.0
            base_tts_total = base_tts_first + 180.0
            network_queue = 18.0 + (len(prompt) % 2) * 5.0
            # Playback latency: from turn start (text submitted) to first audio buffer playback scheduled start
            playback_latency = round(base_llm_end + base_tts_first + network_queue + 30.0, 2)

            await asyncio.sleep(0.01)  # Yield to event loop

            turn.llm_first_token_ms = round(base_llm_first, 2)
            turn.llm_end_ms = round(base_llm_end, 2)
            turn.tts_first_chunk_ms = round(base_tts_first, 2)
            turn.tts_total_ms = round(base_tts_total, 2)
            turn.network_queue_ms = round(network_queue, 2)
            turn.playback_latency_ms = playback_latency

            if simulate_interrupt:
                turn.interruption_latency_ms = 12.5
                turn.status = "interrupted"

            turn.total_turn_ms = round(1000 * (time.monotonic() - start_time) + playback_latency, 2)
            return turn

        # Real execution using OpenVoiceChat components
        try:
            from openvoicechat.conversation import Conversation
            from run_voice_chat import voice_profile

            config, speech = voice_profile(
                voice=self.voice,
                language="zh",
                tts_steps=steps,
                tts_engine=self.tts_engine,
            )
            timings: dict[str, float] = {}

            def on_event(kind: str, **data: Any):
                if kind == "timing":
                    stage = data.get("stage")
                    ms = data.get("milliseconds")
                    if stage and ms is not None:
                        timings[stage] = float(ms)
                elif kind == "interrupted":
                    dur = data.get("duration_ms")
                    if dur is not None:
                        timings["interrupt"] = float(dur)

            conv = Conversation(
                speech,
                config=config,
                on_event=on_event,
                verbose=False,
            )
            try:
                send_task = asyncio.create_task(conv.send_text(prompt))
                first_chunk_received_at = None
                while True:
                    pcm = await conv.output.get()
                    if pcm is None:
                        break
                    if first_chunk_received_at is None:
                        first_chunk_received_at = time.monotonic()
                await send_task
            finally:
                await conv.client.close()
                await speech.aclose()

            turn.llm_first_token_ms = timings.get("llm_first")
            turn.llm_end_ms = timings.get("llm")
            turn.tts_first_chunk_ms = timings.get("tts")
            if first_chunk_received_at:
                server_dur = (timings.get("asr", 0) + (turn.llm_end_ms or 0) + (turn.tts_first_chunk_ms or 0))
                elapsed_ms = round(1000 * (first_chunk_received_at - start_time), 2)
                turn.network_queue_ms = max(0.0, elapsed_ms - server_dur)
                turn.playback_latency_ms = round(elapsed_ms + 30.0, 2)
            if "interrupt" in timings:
                turn.interruption_latency_ms = timings["interrupt"]
            turn.total_turn_ms = round(1000 * (time.monotonic() - start_time), 2)
        except asyncio.CancelledError:
            turn.status = "cancelled"
            turn.error = "Cancelled during execution"
        except Exception as exc:  # noqa: BLE001
            turn.status = "failed"
            turn.error = str(exc)

        return turn

    async def run_suite(
        self,
        prompts: list[str] | None = None,
        *,
        runs_per_prompt: int = 1,
        tts_steps: int | None = None,
        simulate_interrupt: bool = False,
    ) -> BenchmarkReport:
        """Run multiple benchmark turns over the prompt set and aggregate results."""
        test_prompts = prompts or DEFAULT_BENCHMARK_PROMPTS
        steps = tts_steps or self.tts_steps
        turns: list[BenchmarkTurn] = []

        sample_idx = 1
        for p in test_prompts:
            for _ in range(runs_per_prompt):
                t = await self.run_turn(
                    p,
                    sample_idx,
                    tts_steps=steps,
                    simulate_interrupt=simulate_interrupt,
                )
                turns.append(t)
                sample_idx += 1

        completed = [t for t in turns if t.status == "completed"]
        cancelled = [t for t in turns if t.status == "cancelled"]
        failed = [t for t in turns if t.status == "failed"]

        def get_stat(extractor):
            vals = [extractor(t) for t in completed if extractor(t) is not None]
            return calculate_statistics(vals)

        summary = {
            "total_turns": len(turns),
            "completed_turns": len(completed),
            "cancelled_turns": len(cancelled),
            "failed_turns": len(failed),
            "metrics": {
                "llm_first_token_ms": get_stat(lambda t: t.llm_first_token_ms),
                "llm_end_ms": get_stat(lambda t: t.llm_end_ms),
                "tts_first_chunk_ms": get_stat(lambda t: t.tts_first_chunk_ms),
                "tts_total_ms": get_stat(lambda t: t.tts_total_ms),
                "playback_latency_ms": get_stat(lambda t: t.playback_latency_ms),
                "interruption_latency_ms": get_stat(lambda t: t.interruption_latency_ms),
                "total_turn_ms": get_stat(lambda t: t.total_turn_ms),
            },
        }

        config = {
            "tts_engine": self.tts_engine,
            "tts_steps": steps,
            "voice": self.voice,
            "llm_model": self.llm_model,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "runs_per_prompt": runs_per_prompt,
            "mock": self.mock,
        }

        return BenchmarkReport(
            environment=collect_environment_info(),
            config=config,
            turns=turns,
            summary=summary,
        )

    async def compare_steps(
        self,
        steps_list: Sequence[int] = (4, 8),
        prompts: list[str] | None = None,
        runs_per_prompt: int = 1,
    ) -> dict[str, Any]:
        """Run benchmarks for multiple step settings and produce a comparative summary."""
        reports: dict[int, BenchmarkReport] = {}
        for s in steps_list:
            reports[s] = await self.run_suite(
                prompts=prompts,
                runs_per_prompt=runs_per_prompt,
                tts_steps=s,
            )

        comparison: dict[str, Any] = {
            "environment": collect_environment_info(),
            "steps_compared": list(steps_list),
            "reports": {s: r.to_dict() for s, r in reports.items()},
            "comparison_summary": {},
        }

        # Compare metrics between step settings (e.g. 4 vs 8)
        if 4 in reports and 8 in reports:
            r4 = reports[4].summary.get("metrics", {})
            r8 = reports[8].summary.get("metrics", {})
            tts_p50_4 = r4.get("tts_first_chunk_ms", {}).get("p50", 0.0)
            tts_p50_8 = r8.get("tts_first_chunk_ms", {}).get("p50", 0.0)
            play_p50_4 = r4.get("playback_latency_ms", {}).get("p50", 0.0)
            play_p50_8 = r8.get("playback_latency_ms", {}).get("p50", 0.0)

            tts_speedup_pct = round(((tts_p50_8 - tts_p50_4) / tts_p50_8 * 100), 1) if tts_p50_8 > 0 else 0.0
            playback_speedup_pct = round(((play_p50_8 - play_p50_4) / play_p50_8 * 100), 1) if play_p50_8 > 0 else 0.0

            comparison["comparison_summary"] = {
                "tts_first_chunk_p50": {
                    "4_steps": tts_p50_4,
                    "8_steps": tts_p50_8,
                    "speedup_percent": tts_speedup_pct,
                },
                "playback_latency_p50": {
                    "4_steps": play_p50_4,
                    "8_steps": play_p50_8,
                    "speedup_percent": playback_speedup_pct,
                },
            }

        return comparison


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenVoiceChat 延迟基准评测工具")
    parser.add_argument("--steps", type=int, default=8, help="GPT-SoVITS 推理步数 (默认: 8)")
    parser.add_argument("--compare-steps", type=str, default=None, help="对比不同推理步数 (如: '4,8')")
    parser.add_argument("--runs", type=int, default=1, help="每个固定样本的重复轮数 (默认: 1)")
    parser.add_argument("--mock", action="store_true", help="使用模拟/基准模式执行 (无需外部 GPU 服务)")
    parser.add_argument("--engine", type=str, default="gpt_sovits", help="TTS 引擎 (gpt_sovits / cosyvoice)")
    parser.add_argument("--voice", type=str, default="chinatsu", help="TTS 音色名称")
    parser.add_argument("--output", type=str, default=None, help="导出 JSON 格式报告文件路径")
    parser.add_argument("--markdown", type=str, default=None, help="导出 Markdown 格式报告文件路径")
    args = parser.parse_args()

    runner = BenchmarkRunner(
        mock=args.mock,
        tts_engine=args.engine,
        tts_steps=args.steps,
        voice=args.voice,
    )

    if args.compare_steps:
        steps = [int(s.strip()) for s in args.compare_steps.split(",") if s.strip()]
        print(f"正在运行步数基准对比: {steps} (mock={args.mock})...")
        comp = asyncio.run(runner.compare_steps(steps, runs_per_prompt=args.runs))
        print("\n=== GPT-SoVITS 推理步数基准对比汇总 ===")
        summ = comp.get("comparison_summary", {})
        tts_s = summ.get("tts_first_chunk_p50", {})
        play_s = summ.get("playback_latency_p50", {})
        print(f"TTS 首包 P50: 4 步={tts_s.get('4_steps')}ms vs 8 步={tts_s.get('8_steps')}ms (提速 {tts_s.get('speedup_percent')}%)")
        print(f"端到端发声 P50: 4 步={play_s.get('4_steps')}ms vs 8 步={play_s.get('8_steps')}ms (提速 {play_s.get('speedup_percent')}%)")
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(comp, f, ensure_ascii=False, indent=2)
            print(f"报告已保存至 {args.output}")
    else:
        print(f"正在运行单项延迟基准测试 (steps={args.steps}, mock={args.mock})...")
        report = asyncio.run(runner.run_suite(runs_per_prompt=args.runs))
        md = report.format_markdown()
        print("\n" + md)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(report.to_json())
            print(f"JSON 报告已保存至 {args.output}")
        if args.markdown:
            with open(args.markdown, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"Markdown 报告已保存至 {args.markdown}")


if __name__ == "__main__":
    main()
