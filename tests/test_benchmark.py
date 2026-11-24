import json

import pytest

from openvoicechat.benchmark import (
    DEFAULT_BENCHMARK_PROMPTS,
    BenchmarkRunner,
    calculate_percentile,
    calculate_statistics,
    collect_environment_info,
)


def test_calculate_percentile():
    assert calculate_percentile([], 50) == 0.0
    assert calculate_percentile([42.0], 50) == 42.0
    assert calculate_percentile([42.0], 95) == 42.0

    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert calculate_percentile(data, 0) == 10.0
    assert calculate_percentile(data, 50) == 30.0
    assert calculate_percentile(data, 100) == 50.0
    # 95th percentile of 5 points: index is 4 * 0.95 = 3.8 -> 40*0.2 + 50*0.8 = 48.0
    assert calculate_percentile(data, 95) == pytest.approx(48.0)


def test_calculate_statistics():
    empty_stat = calculate_statistics([])
    assert empty_stat["count"] == 0
    assert empty_stat["min"] == 0.0
    assert empty_stat["max"] == 0.0
    assert empty_stat["avg"] == 0.0
    assert empty_stat["p50"] == 0.0
    assert empty_stat["p95"] == 0.0

    latencies = [100.0, 150.0, 200.0, 300.0, 500.0]
    stats = calculate_statistics(latencies)
    assert stats["count"] == 5
    assert stats["min"] == 100.0
    assert stats["max"] == 500.0
    assert stats["avg"] == 250.0
    assert stats["p50"] == 200.0
    assert stats["p95"] > 200.0


def test_collect_environment_info():
    env = collect_environment_info()
    assert "platform" in env
    assert "system" in env
    assert "python_version" in env
    assert "timestamp" in env


@pytest.mark.asyncio
async def test_benchmark_runner_mock_suite():
    runner = BenchmarkRunner(mock=True, tts_steps=8)
    report = await runner.run_suite(prompts=DEFAULT_BENCHMARK_PROMPTS[:3], runs_per_prompt=1)

    assert report.summary["total_turns"] == 3
    assert report.summary["completed_turns"] == 3
    assert report.summary["cancelled_turns"] == 0
    assert report.summary["failed_turns"] == 0

    metrics = report.summary["metrics"]
    assert metrics["llm_first_token_ms"]["count"] == 3
    assert metrics["llm_first_token_ms"]["p50"] > 0
    assert metrics["tts_first_chunk_ms"]["p50"] > 0
    assert metrics["playback_latency_ms"]["p50"] > 0

    json_str = report.to_json()
    loaded = json.loads(json_str)
    assert loaded["config"]["tts_steps"] == 8
    assert len(loaded["turns"]) == 3

    markdown = report.format_markdown()
    assert "# OpenVoiceChat 延迟基准评测报告" in markdown
    assert "P50 (中位数)" in markdown


@pytest.mark.asyncio
async def test_benchmark_runner_isolation_of_uncompleted_turns():
    runner = BenchmarkRunner(mock=True, tts_steps=8)

    turn_normal = await runner.run_turn("正常测试", 1)
    turn_cancelled = await runner.run_turn("取消测试", 2, simulate_cancel=True)
    turn_failed = await runner.run_turn("失败测试", 3, simulate_failure=True)

    assert turn_normal.status == "completed"
    assert turn_cancelled.status == "cancelled"
    assert turn_failed.status == "failed"

    turns = [turn_normal, turn_cancelled, turn_failed]
    completed = [t for t in turns if t.status == "completed"]
    cancelled = [t for t in turns if t.status == "cancelled"]
    failed = [t for t in turns if t.status == "failed"]

    # Only completed turn is counted in metrics
    latencies = [t.playback_latency_ms for t in completed if t.playback_latency_ms is not None]
    stats = calculate_statistics(latencies)
    assert stats["count"] == 1
    assert stats["p50"] == turn_normal.playback_latency_ms
    assert len(cancelled) == 1
    assert len(failed) == 1


@pytest.mark.asyncio
async def test_benchmark_compare_steps_4_vs_8():
    runner = BenchmarkRunner(mock=True)
    prompts = ["一句话测试", "两句话测试"]
    comp = await runner.compare_steps(steps_list=[4, 8], prompts=prompts, runs_per_prompt=1)

    assert comp["steps_compared"] == [4, 8]
    summary = comp["comparison_summary"]
    tts_summary = summary["tts_first_chunk_p50"]
    playback_summary = summary["playback_latency_p50"]

    assert tts_summary["4_steps"] < tts_summary["8_steps"]
    assert tts_summary["speedup_percent"] > 0
    assert playback_summary["4_steps"] < playback_summary["8_steps"]
    assert playback_summary["speedup_percent"] > 0
