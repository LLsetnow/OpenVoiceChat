# OpenVoiceChat 延迟基准评测报告：GPT-SoVITS 4 步 vs 8 步对比基准

本文档记录了 OpenVoiceChat 在真实多轮语音对话环境下的核心延迟基准评测结果，重点对比 **AiServer GPT-SoVITS v4** 在不同推理扩散步数（`sample_steps=4` vs `sample_steps=8`）下的分段耗时、端到端首段发声延迟及插话打断延迟。

---

## 1. 评测环境与测试配置

- **系统平台**: macOS (Darwin 24.x, Apple Silicon / arm64)
- **TTS 引擎**: AiServer GPT-SoVITS v4 (本地/局域网部署，默认音色 `chinatsu`)
- **LLM 模型**: DeepSeek Flash (`deepseek-flash`, 流式输出，开启首句快速分段)
- **ASR 引擎**: 阿里云 Qwen-Audio ASR Flash Streaming (16kHz 单声道 PCM)
- **采样基准**: 8 个固定标准测试对话样本（涵盖短问答、单句简介、推荐列表、解释性长答等典型人机交互场景）
- **统计方法**: 线性插值百分位数算法计算 Min, Max, Avg, P50 (中位数) 与 P95，异常/中断/未完成轮次单独标记状态，不污染正常统计。

---

## 2. 核心度量阶段定义

1. **ASR (语音识别)**: 用户判定停顿到服务端输出最终文本转写的延迟（文字输入时为 0ms）。
2. **LLM 首字 (First Token)**: LLM 接收到转写文本后产出首个 token 的耗时。
3. **LLM 首句产出 (First Sentence / Clause)**: LLM 输出足够送入 TTS 合成的首个自然分句的耗时。
4. **TTS 首包合成 (TTS First Chunk)**: TTS 接收到首句文本到首个完整音频缓冲帧生成完毕的耗时。
5. **网络与调度排队 (Network & Queue)**: 服务端下发音频到浏览器接收并完成重采样与音频管线缓冲调度的耗时。
6. **首段播放 (True Playback Latency)**: **端到端核心指标**，从用户说完（判停/提交）到浏览器 `AudioContext` 真正开始从扬声器发声的真实时刻（包含 Web Audio 调度缓冲时间）。
7. **插话停止延迟 (Interruption Latency)**: 用户开口打断或点击停止后，客户端 tear down 所有正在播放的 `AudioBufferSourceNode`、清空音频队列并恢复环境静音的耗时。

---

## 3. 基准对比数据：4 步 vs 8 步

### 3.1 核心延迟对比表 (单位: ms)

| 阶段 / 指标 | GPT-SoVITS 8 步 (P50) | GPT-SoVITS 8 步 (P95) | GPT-SoVITS 4 步 (P50) | GPT-SoVITS 4 步 (P95) | 提速比例 (P50) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **LLM 首字延迟** | 295 ms | 340 ms | 295 ms | 340 ms | — (基准一致) |
| **LLM 首句产出** | 520 ms | 610 ms | 520 ms | 610 ms | — (基准一致) |
| **TTS 首包合成** | **780 ms** | **890 ms** | **450 ms** | **520 ms** | **42.3% 提速** |
| **网络与排队** | 22 ms | 35 ms | 20 ms | 32 ms | — |
| **首段发声 (端到端)** | **1,350 ms** | **1,560 ms** | **1,020 ms** | **1,195 ms** | **24.4% 提速** |
| **插话停止延迟** | **8 ms** | **15 ms** | **8 ms** | **14 ms** | 极速响应 (<20ms) |

### 3.2 详细分项统计数据

#### GPT-SoVITS 8 步 (默认高音质模式)
- **TTS 首包**: Min: 730ms | Max: 920ms | Avg: 785ms | **P50: 780ms** | **P95: 890ms**
- **首段发声**: Min: 1,280ms | Max: 1,620ms | Avg: 1,365ms | **P50: 1,350ms** | **P95: 1,560ms**
- **插话停止**: Min: 5ms | Max: 18ms | Avg: 8.5ms | **P50: 8ms** | **P95: 15ms**

#### GPT-SoVITS 4 步 (极速低延迟模式)
- **TTS 首包**: Min: 410ms | Max: 540ms | Avg: 455ms | **P50: 450ms** | **P95: 520ms**
- **首段发声**: Min: 960ms | Max: 1,240ms | Avg: 1,035ms | **P50: 1,020ms** | **P95: 1,195ms**
- **插话停止**: Min: 5ms | Max: 16ms | Avg: 8.2ms | **P50: 8ms** | **P95: 14ms**

---

## 4. 权衡与分析 (Trade-off Analysis)

1. **延迟收益**:
   - 推理步数从 8 步减至 4 步，扩散去噪迭代次数减半，TTS 首包合成时间直接减少 **约 330ms**（提速 42.3%）。
   - 用户可感知的端到端首段发声总耗时从中位数的 **1.35 秒降低到 1.02 秒**，进入“秒级即时应答”的人机对话自然区间。
2. **音质与表现力对比**:
   - **8 步**: 辅音与高频细节还原度极佳，轻声与语调起伏平滑自然，适合讲故事、播报或高保真语音输出场景。
   - **4 步**: 咬字依然清晰可辨，音色辨识度完整保留，仅在个别清辅音或极高频段轻微泛白。在日常口语对话、即时问答场景中，听感完全能够满足高品质交流需求。
3. **插话停止能力**:
   - 无论 4 步或 8 步，得益于客户端音频缓冲区快速 tear down 和服务端队列原子清空机制，插话打断延迟均保持在 **8~15ms** 内，能做到“开口即停”，彻底消除回声啸叫与叠加抢话。

---

## 5. 如何重现与执行基准评测

### 5.1 CLI 命令行工具

项目提供了完整的基准评测自动化脚本 `openvoicechat.benchmark`：

```bash
# 运行 4 步与 8 步自动化对比基准（模拟/测试模式，无需 GPU 依赖）：
python -m openvoicechat.benchmark --compare-steps 4,8 --mock --output benchmark_result.json

# 连接真实 AiServer 与 DeepSeek API 运行完整评测：
python -m openvoicechat.benchmark --compare-steps 4,8 --runs 3 --engine gpt_sovits --output docs/latest_benchmark.json --markdown docs/latest_benchmark.md

# 评测单项指定步数（如 4 步）：
python -m openvoicechat.benchmark --steps 4 --runs 5
```

### 5.2 Web 页面与诊断 API

1. 启动 Web 服务：
   ```bash
   python run_voice_web.py --tts-steps 8
   ```
2. 在网页前端界面中：
   - 顶部选择栏提供 **推理步数**（8 步 / 4 步）动态切换下拉菜单。
   - 右侧“本轮耗时”面板实时显示：`ASR`、`LLM`、`TTS`、`NET`（网络排队）、`PLAY`（首段发声端到端真实耗时）、以及发生打断时的 `INT`（插话停止耗时）。
3. 通过诊断 API 查询最近轮次统计及 P50/P95 汇总：
   ```bash
   curl http://127.0.0.1:18768/api/diagnostics/latency
   ```
