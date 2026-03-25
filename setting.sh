#!/bin/sh
set -eu

# Optional overrides:
# - CODEX_HOME
# - CODEX_BINARY
# - CODEX_MODEL
# - CODEX_REASONING_EFFORT
# - CODEX_REVIEWER_FRAMEWORK_ROOT
# - ENABLE_EXA
# - EXA_API_KEY
# - SHRIMP_DATA_DIR

find_latest_codex_dir() {
  latest_dir=""
  for candidate in "$HOME"/.vscode/extensions/openai.chatgpt-*-darwin-arm64/bin/macos-aarch64; do
    [ -d "$candidate" ] || continue
    if [ -z "$latest_dir" ] || [ "$candidate" -nt "$latest_dir" ]; then
      latest_dir="$candidate"
    fi
  done
  printf '%s' "$latest_dir"
}

prepend_path_if_dir() {
  current_path=$1
  candidate_dir=$2

  if [ -z "$candidate_dir" ] || [ ! -d "$candidate_dir" ]; then
    printf '%s' "$current_path"
    return
  fi

  case ":$current_path:" in
    *":$candidate_dir:"*) printf '%s' "$current_path" ;;
    *) printf '%s:%s' "$candidate_dir" "$current_path" ;;
  esac
}

append_path_if_dir() {
  current_path=$1
  candidate_dir=$2

  if [ -z "$candidate_dir" ] || [ ! -d "$candidate_dir" ]; then
    printf '%s' "$current_path"
    return
  fi

  case ":$current_path:" in
    *":$candidate_dir:"*) printf '%s' "$current_path" ;;
    *) printf '%s:%s' "$current_path" "$candidate_dir" ;;
  esac
}

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CODEX_HOME_DIR=${CODEX_HOME:-"$HOME/.codex"}
CONFIG_PATH="$CODEX_HOME_DIR/config.toml"
CODEX_BIN_DIR="$CODEX_HOME_DIR/bin"
CODEX_SCRIPTS_DIR="$CODEX_HOME_DIR/scripts"
CODEX_SKILLS_DIR="$CODEX_HOME_DIR/skills"

CONDA_PATH=${CONDA_EXE:-}
if [ -z "$CONDA_PATH" ]; then
  CONDA_PATH=$(command -v conda 2>/dev/null || true)
fi

NPX_PATH=$(command -v npx 2>/dev/null || true)
PYTHON_PATH=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)

if [ -z "$CONDA_PATH" ]; then
  echo "setting.sh: conda not found. Export CONDA_EXE or add conda to PATH." >&2
  exit 1
fi

if [ -z "$NPX_PATH" ]; then
  echo "setting.sh: npx not found. Please install Node.js or add npx to PATH." >&2
  exit 1
fi

if [ -z "$PYTHON_PATH" ]; then
  echo "setting.sh: python3/python not found. Please install Python or add it to PATH." >&2
  exit 1
fi

CONDA_BIN_DIR=$(dirname "$CONDA_PATH")
NPX_BIN_DIR=$(dirname "$NPX_PATH")
VSCODE_CODEX_DIR=$(find_latest_codex_dir)

BASE_ENV_PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
BASE_ENV_PATH=$(prepend_path_if_dir "$BASE_ENV_PATH" "$NPX_BIN_DIR")
BASE_ENV_PATH=$(prepend_path_if_dir "$BASE_ENV_PATH" "$CONDA_BIN_DIR")
MCP_ENV_PATH=$(append_path_if_dir "$BASE_ENV_PATH" "$VSCODE_CODEX_DIR")
REVIEWER_ENV_PATH="$BASE_ENV_PATH"

REVIEWER_ROOT=${CODEX_REVIEWER_FRAMEWORK_ROOT:-"$PROJECT_ROOT"}
CODEX_BINARY_PATH=${CODEX_BINARY:-"$CODEX_HOME_DIR/bin/codex-latest"}
CODEX_MODEL_VALUE=${CODEX_MODEL:-"gpt-5.4"}
CODEX_REASONING_EFFORT_VALUE=${CODEX_REASONING_EFFORT:-"xhigh"}
SHRIMP_DATA_DIR_VALUE=${SHRIMP_DATA_DIR:-".shrimp"}
ENABLE_EXA_VALUE=${ENABLE_EXA:-"0"}
EXA_API_KEY_VALUE=${EXA_API_KEY:-"your-exa-api-key"}

mkdir -p "$CODEX_HOME_DIR"
mkdir -p "$CODEX_BIN_DIR"
mkdir -p "$CODEX_SCRIPTS_DIR"
mkdir -p "$CODEX_SKILLS_DIR"

cat > "$CONFIG_PATH" <<EOF
model = "${CODEX_MODEL_VALUE}"
model_reasoning_effort = "${CODEX_REASONING_EFFORT_VALUE}"

# ~/.codex/config.toml 的 multi-codex MCP 配置示例（macOS + conda）
# 说明：
# 1. MCP 统一通过本机 conda 或 Python 启动。
# 2. Node 仍使用当前机器上的 npx。
# 3. code-index 依赖 uvx，如需启用请单独扩展。
# 4. codex-reviewer 通过全局共享 wrapper 启动，脚本位于 ~/.codex/scripts/。
# 5. wrapper 会负责捕获真实 conversation_id，并写入目标项目本地 .codex/。

[mcp_servers]

[mcp_servers.sequential-thinking]
type = "stdio"
command = "${CONDA_PATH}"
args = ["run", "--no-capture-output", "-n", "base", "${NPX_PATH}", "-y", "@modelcontextprotocol/server-sequential-thinking"]
env = { PATH = "${MCP_ENV_PATH}", HOME = "${HOME}" }

[mcp_servers.shrimp-task-manager]
type = "stdio"
command = "${CONDA_PATH}"
args = ["run", "--no-capture-output", "-n", "base", "${NPX_PATH}", "-y", "mcp-shrimp-task-manager"]
env = { PATH = "${MCP_ENV_PATH}", HOME = "${HOME}", DATA_DIR = "${SHRIMP_DATA_DIR_VALUE}", TEMPLATES_USE = "zh", ENABLE_GUI = "false" }

[mcp_servers.codex-reviewer]
type = "stdio"
command = "${PYTHON_PATH}"
args = ["${CODEX_SCRIPTS_DIR}/codex_reviewer_mcp.py"]
env = { PATH = "${REVIEWER_ENV_PATH}", HOME = "${HOME}", CODEX_BINARY = "${CODEX_BINARY_PATH}", CODEX_REVIEWER_FRAMEWORK_ROOT = "${REVIEWER_ROOT}" }

[mcp_servers.chrome-devtools]
type = "stdio"
command = "${CONDA_PATH}"
args = ["run", "--no-capture-output", "-n", "base", "${NPX_PATH}", "chrome-devtools-mcp@latest"]
env = { PATH = "${MCP_ENV_PATH}", HOME = "${HOME}" }
EOF

if [ "$ENABLE_EXA_VALUE" = "1" ]; then
  cat >> "$CONFIG_PATH" <<EOF

[mcp_servers.exa]
type = "stdio"
command = "${CONDA_PATH}"
args = ["run", "--no-capture-output", "-n", "base", "${NPX_PATH}", "-y", "exa-mcp-server"]
env = { PATH = "${MCP_ENV_PATH}", HOME = "${HOME}", EXA_API_KEY = "${EXA_API_KEY_VALUE}" }
EOF
fi

echo "Generated ${CONFIG_PATH}"

cp "${PROJECT_ROOT}/base/codex-latest" "$CODEX_BIN_DIR/codex-latest"
chmod +x "$CODEX_BIN_DIR/codex-latest"
echo "Copied codex-latest to ${CODEX_BIN_DIR}"

cp -r "${PROJECT_ROOT}/skills/." "$CODEX_SKILLS_DIR/"
echo "Copied skills to ${CODEX_SKILLS_DIR}"

cp -r "${PROJECT_ROOT}/.scripts/." "$CODEX_SCRIPTS_DIR/"
echo "Copied scripts to ${CODEX_SCRIPTS_DIR}"

