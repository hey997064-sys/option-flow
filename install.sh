#!/usr/bin/env bash
#
# option-flow 一键安装（MCP server）。在任意电脑上：建 venv → 装依赖 → 自检 →
# 把 MCP server 条目写进 Claude Desktop 配置（按本机实际克隆位置算绝对路径）。
#
# 用法：
#   ./install.sh
#
# 路径全部相对本脚本所在目录计算，无任何硬编码个人路径——换电脑/换用户名照样工作。
# 写哪个配置文件可用环境变量覆盖（默认 macOS Claude Desktop）：
#   OPTION_FLOW_CONFIG_OVERRIDE=/path/to/config.json ./install.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "▸ option-flow 安装目录：$SCRIPT_DIR"

# 1. python3 ------------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 未找到 python3，请先安装 Python 3.9+。"
  exit 1
fi

# 2. venv（幂等：已存在则复用）------------------------------------------------
if [ ! -x ".venv/bin/python" ]; then
  echo "▸ 创建虚拟环境 .venv ..."
  python3 -m venv .venv
else
  echo "▸ 复用已有 .venv"
fi

# 3. 依赖 ---------------------------------------------------------------------
echo "▸ 安装依赖 ..."
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt

# 4. 自检（longbridge 找得到 / 已登录 / 数据可取）——不阻断安装 ---------------
echo "▸ 运行自检 ..."
set +e
.venv/bin/python mcp_server.py --check
CHECK_RC=$?
set -e

# 5. 写 Claude Desktop 配置（绝对路径按本机算）--------------------------------
DEFAULT_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
CONFIG="${OPTION_FLOW_CONFIG_OVERRIDE:-$DEFAULT_CONFIG}"
PY="$SCRIPT_DIR/.venv/bin/python"
SERVER="$SCRIPT_DIR/mcp_server.py"

echo "▸ 写入 MCP 配置：$CONFIG"
"$PY" install_config.py "$CONFIG" "$PY" "$SERVER"

# 6. 收尾 ---------------------------------------------------------------------
echo
echo "✅ 安装完成。重启 Claude Desktop 后，工具栏会出现 option_flow 工具。"
echo "   对你说「option flow NVDA」即可出报告。"
if [ "$CHECK_RC" -ne 0 ]; then
  echo
  echo "⚠️  自检未全绿（见上）。按其中「修复」处理后再用；最常见是 longbridge auth login。"
fi
