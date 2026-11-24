#!/bin/zsh
set -e

# 设置终端窗口标题
printf '\033]0;OpenVoiceChat 实时语音对话\007'

# 错误处理函数
fail() {
  print -ru2 -- ""
  print -ru2 -- "❌ $1"
  print -ru2 -- ""
  if [[ -t 0 && -t 1 ]]; then
    read -r '?按 Enter 键关闭窗口。'
  fi
  exit 1
}

# 切换到项目根目录
PROJ_DIR="${0:A:h}"
cd -- "$PROJ_DIR" || fail "无法进入 OpenVoiceChat 项目目录：$PROJ_DIR"

# 补充基础 PATH（Finder 双击启动时可能缺少 Homebrew 或本地 bin）
export PATH="$PATH:/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin"

print -r -- "=================================================="
print -r -- "           OpenVoiceChat 实时语音交互系统          "
print -r -- "=================================================="

# 1. 检查并加载环境变量
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi

if [[ -z "${DEEPSEEK_API_KEY:-}" || -z "${ALIYUN_API_KEY:-}" ]]; then
  fail "缺少必要的 API 密钥（DEEPSEEK_API_KEY 或 ALIYUN_API_KEY）。请检查项目根目录下的 .env 文件。"
fi

# 2. 检查 Python 虚拟环境
if [[ ! -x ".venv/bin/python" ]]; then
  print -r -- "⚠️  未检测到 .venv 虚拟环境，尝试通过 uv 自动初始化..."
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.12 || fail "创建 .venv 虚拟环境失败。"
    uv pip install -e '.[aliyun,web]' || fail "安装项目依赖失败。"
  else
    fail "未找到 .venv 虚拟环境且系统未安装 uv。请先在终端执行：uv venv --python 3.12 && uv pip install -e '.[aliyun,web]'"
  fi
fi

# 3. 检查 AiServer (GPT-SoVITS) 连通性
if ! curl -s -m 2 http://127.0.0.1:19889/voices >/dev/null 2>&1; then
  print -r -- "⚠️  [提示] 未检测到 AiServer (GPT-SoVITS v4) 在 127.0.0.1:19889 运行。"
  print -r -- "   如需语音合成发声，请打开 AiServer.app 启动 GPT-SoVITS 服务。"
  print -r -- "--------------------------------------------------"
else
  print -r -- "✅ AiServer GPT-SoVITS 连接正常 (127.0.0.1:19889)"
fi

# 4. 选择启动模式
MODE="web"

if [[ "$1" == "--cli" || "$1" == "-c" ]]; then
  MODE="cli"
elif [[ -t 0 ]]; then
  print -r -- "启动选项："
  print -r -- " [1] Web 控制面板（推荐，浏览器可视化状态与设备切换）"
  print -r -- " [2] 纯终端语音对话（命令行交互）"
  print -n -- "正在启动 Web 模式（3秒内按 2 切换为终端模式）... "

  if read -t 3 -k 1 ch; then
    print ""
    if [[ "$ch" == "2" ]]; then
      MODE="cli"
    fi
  else
    print ""
  fi
fi

if [[ "$MODE" == "cli" ]]; then
  print -r -- "🚀 启动终端纯语音对话模式 (run_voice_chat.py)..."
  print -r -- "提示：按 Ctrl+C 退出对话。"
  print -r -- "=================================================="
  .venv/bin/python run_voice_chat.py -v
else
  PORT=18768
  URL="http://127.0.0.1:$PORT"

  .venv/bin/python -m openvoicechat.migrate || fail "初始化本地对话记录数据库失败。"

  # 检查端口是否已被占用
  if lsof -i :$PORT >/dev/null 2>&1; then
    print -r -- "ℹ️  检测到 OpenVoiceChat Web 服务已在运行中 (端口 $PORT)。"
    print -r -- "🌐 正在为你打开浏览器页面：$URL"
    open "$URL"
    print -r -- ""
    print -r -- "提示：若需重启服务，请先在原终端中关闭，或执行: kill \$(lsof -ti :$PORT)"
    if [[ -t 0 && -t 1 ]]; then
      read -r '?按 Enter 键关闭此窗口。'
    fi
    exit 0
  fi

  print -r -- "🚀 正在启动 Web 服务: $URL"
  print -r -- "正在打开浏览器..."
  print -r -- "提示：在终端按 Ctrl+C 可停止服务。"
  print -r -- "=================================================="

  # 延迟 1.2 秒后自动打开浏览器访问 Web 界面
  (sleep 1.2 && open "$URL") &

  .venv/bin/python run_voice_web.py --port "$PORT"
fi

exit_code=$?
if [[ $exit_code -ne 0 ]]; then
  fail "进程异常退出 (退出码: $exit_code)。"
fi
