# Codex Reviewer 迁移文档

## 目标

迁移完成后，你应当得到下面这套状态：

1. 用户目录里的 `~/.codex/` 或 `%USERPROFILE%\.codex\` 已生成新的 `config.toml`
2. 全局共享 wrapper 已复制到 `~/.codex/scripts/` 或 `%USERPROFILE%\.codex\scripts\`
3. 全局 skill 已复制到 `~/.codex/skills/` 或 `%USERPROFILE%\.codex\skills\`
4. 目标项目仍然保留自己的 `AGENTS.md` 和 `CODEX.md`
5. `codex-reviewer` 运行时把产物写回目标项目本地 `.codex/`

## 迁移范围

### 仓库内的源文件

这些文件是迁移的 source of truth：

| 路径 | 用途 |
| --- | --- |
| `AGENTS.md` | reviewer 侧规范，约束审查职责、产物和输出格式 |
| `CODEX.md` | 主 Codex 侧规范，约束如何调用 reviewer |
| `base/codex-latest` | macOS 下发到 `~/.codex/bin/codex-latest` 的包装脚本源文件 |
| `base/codex-latest.cmd` | Windows 下发到 `%USERPROFILE%\.codex\bin\codex-latest.cmd` 的包装脚本源文件 |
| `.scripts/codex_reviewer_mcp.py` | 全局共享 wrapper 的源文件 |
| `skills/multi-codex-orchestrator/SKILL.md` | 主 Codex 编排 skill |
| `skills/multi-codex-orchestrator/agents/openai.yaml` | 对应 skill 配置 |
| `skills/codex-reviewer-workflow/SKILL.md` | reviewer 工作流 skill |
| `skills/codex-reviewer-workflow/agents/openai.yaml` | 对应 skill 配置 |
| `setting.sh` | macOS / Linux 迁移脚本 |
| `setting.ps1` | Windows PowerShell 迁移脚本 |

### 迁移后在用户目录生成的文件

迁移脚本会生成或覆盖这些内容：

| 目标路径 | 来源 |
| --- | --- |
| `~/.codex/config.toml` | `setting.sh` 生成 |
| `~/.codex/bin/codex-latest` | 从 `base/codex-latest` 复制 |
| `~/.codex/scripts/codex_reviewer_mcp.py` | 从 `.scripts/` 复制 |
| `~/.codex/skills/...` | 从 `skills/` 复制 |
| `%USERPROFILE%\.codex\config.toml` | `setting.ps1` 生成 |
| `%USERPROFILE%\.codex\bin\codex-latest.cmd` | 从 `base/codex-latest.cmd` 复制 |
| `%USERPROFILE%\.codex\scripts\codex_reviewer_mcp.py` | 从 `.scripts/` 复制 |
| `%USERPROFILE%\.codex\skills\...` | 从 `skills/` 复制 |

### 不建议迁移的内容

这些内容不是迁移源文件，不要从旧机直接复制到新机当配置：

- 项目本地 `.codex/` 里的运行产物，例如 `context-initial.json`、`review-report.md`
- `~/.codex/state_5.sqlite`、`session_index.jsonl`、`sessions/`
- 旧机上的项目绝对路径
- 仓库根目录的 `codex mcp`

说明：

- `codex mcp` 只是手工参考片段，不是当前迁移脚本的 source of truth
- 当前应以 [setting.sh](/Users/asuna/Asuna/study&work/git/agentframework/setting.sh) 和 [setting.ps1](/Users/asuna/Asuna/study&work/git/agentframework/setting.ps1) 生成的配置为准

## 迁移后的运行链路

迁移成功后，实际调用链应该是：

1. 主 Codex 调用 `mcp__codex_reviewer__codex`
2. `config.toml` 启动全局 `codex-reviewer` wrapper
3. wrapper 调用 `CODEX_BINARY`
4. `CODEX_BINARY` 再转发到本机真实 `codex`
5. reviewer 优先读取目标项目根目录的 `AGENTS.md` 和 `CODEX.md`
6. reviewer 把上下文和审查产物写入目标项目本地 `.codex/`

## 前置条件

### macOS

- 已安装 Conda，且 `conda` 可执行
- 已安装 Python 3
- 已安装 Node.js，且 `npx` 可执行
- 已安装带 `codex` 的 VS Code ChatGPT 扩展

### Windows

- 已安装 Conda，且 `conda.exe` 可执行
- 已安装 Python
- 已安装 Node.js，且 `npx.cmd` 可执行
- 已安装带 `codex` 的 VS Code ChatGPT 扩展
- 建议使用 `pwsh` 执行迁移脚本

## macOS 迁移步骤

在仓库根目录执行：

```sh
sh ./setting.sh
```

如果你需要覆盖默认值，可以这样传环境变量：

```sh
CODEX_HOME="$HOME/.codex" \
CODEX_REVIEWER_FRAMEWORK_ROOT="/path/to/your/repo" \
CODEX_BINARY="$HOME/.codex/bin/codex-latest" \
CODEX_MODEL="gpt-5.4" \
CODEX_REASONING_EFFORT="xhigh" \
SHRIMP_DATA_DIR=".shrimp" \
sh ./setting.sh
```

如果你还想同时启用 `exa`：

```sh
ENABLE_EXA=1 EXA_API_KEY="your-key" sh ./setting.sh
```

执行完成后，脚本会：

- 生成 `~/.codex/config.toml`
- 复制 `base/codex-latest` 到 `~/.codex/bin/codex-latest`
- 复制 `.scripts/codex_reviewer_mcp.py` 到 `~/.codex/scripts/`
- 复制 `skills/` 到 `~/.codex/skills/`

## Windows 迁移步骤

在仓库根目录执行：

```powershell
pwsh -File .\setting.ps1
```

如需覆盖默认值：

```powershell
$env:CODEX_HOME = "$env:USERPROFILE\.codex"
$env:CODEX_REVIEWER_FRAMEWORK_ROOT = "D:\path\to\your\repo"
$env:CODEX_BINARY = "$env:USERPROFILE\.codex\bin\codex-latest.cmd"
$env:CODEX_MODEL = "gpt-5.4"
$env:CODEX_REASONING_EFFORT = "xhigh"
$env:SHRIMP_DATA_DIR = ".shrimp"
pwsh -File .\setting.ps1
```

如需启用 `exa`：

```powershell
$env:ENABLE_EXA = "1"
$env:EXA_API_KEY = "your-key"
pwsh -File .\setting.ps1
```

执行完成后，脚本会：

- 生成 `%USERPROFILE%\.codex\config.toml`
- 复制 `base/codex-latest.cmd` 到 `%USERPROFILE%\.codex\bin\codex-latest.cmd`
- 复制 `.scripts/codex_reviewer_mcp.py` 到 `%USERPROFILE%\.codex\scripts\`
- 复制 `skills/` 到 `%USERPROFILE%\.codex\skills\`

## 支持的环境变量

两个迁移脚本支持同一套覆盖点：

| 变量名 | 作用 |
| --- | --- |
| `CODEX_HOME` | 改写全局 `.codex` 根目录 |
| `CODEX_BINARY` | 指定 `codex-reviewer` 最终调用的 `codex` 包装器 |
| `CODEX_MODEL` | 写入 `config.toml` 的默认模型 |
| `CODEX_REASONING_EFFORT` | 写入 `config.toml` 的默认推理强度 |
| `CODEX_REVIEWER_FRAMEWORK_ROOT` | wrapper 的降级文档根目录 |
| `SHRIMP_DATA_DIR` | `shrimp-task-manager` 的数据目录 |
| `ENABLE_EXA` | 是否生成 `exa` MCP 配置，`1` 为启用 |
| `EXA_API_KEY` | `exa` 启用时写入的密钥 |
| `CONDA_EXE` | 手动指定 `conda` 可执行路径 |

## `codex-latest` 的要求

迁移脚本现在会自动把仓库内的 `base/codex-latest` 或 `base/codex-latest.cmd` 复制到全局 `.codex/bin/`。

你的 `codex-latest` 或 `codex-latest.cmd` 至少要满足两件事：

1. 能找到当前机器上真实的 `codex`
2. 能把收到的参数原样透传出去

如果目标机上的 VS Code 扩展目录结构与脚本内的匹配规则不同，你需要更新仓库里的 `base/codex-latest` 或 `base/codex-latest.cmd`，再重新运行迁移脚本。

## 验收清单

迁移完成后，至少检查下面这些项目：

### 文件检查

- `~/.codex/config.toml` 或 `%USERPROFILE%\.codex\config.toml` 已生成
- `~/.codex/bin/codex-latest` 或 `%USERPROFILE%\.codex\bin\codex-latest.cmd` 已生成
- `~/.codex/scripts/codex_reviewer_mcp.py` 或 `%USERPROFILE%\.codex\scripts\codex_reviewer_mcp.py` 已存在
- `~/.codex/skills/multi-codex-orchestrator/` 已存在
- `~/.codex/skills/codex-reviewer-workflow/` 已存在
- `CODEX_BINARY` 指向的文件已存在且可执行

### 配置检查

至少确认 `config.toml` 中存在这些 MCP：

- `sequential-thinking`
- `shrimp-task-manager`
- `codex-reviewer`
- `chrome-devtools`

如果你启用了 `ENABLE_EXA=1`，还应存在：

- `exa`

### 运行检查

在新的 Codex 会话中执行：

```text
codex mcp list
codex mcp get codex-reviewer
```

你应该至少确认：

- `codex-reviewer` 已注册
- `command` 指向本机 Python
- `args` 指向全局 `~/.codex/scripts/codex_reviewer_mcp.py` 或 Windows 对应路径
- `CODEX_BINARY` 指向本机可用的包装脚本

如果目标项目根目录有 `AGENTS.md` 和 `CODEX.md`，再做一次真实调用，确认 reviewer 会把产物写进项目本地 `.codex/`。

## 常见问题

### 1. 迁移后 `codex-reviewer` 启动失败

优先检查：

- `conda`、`python`、`npx` 是否都在当前机器上可执行
- `CODEX_BINARY` 指向的包装脚本是否存在
- 包装脚本最终能否找到真实 `codex`

### 2. reviewer 读到了错误的文档

优先检查：

- 当前工作目录是否真的是目标项目根目录
- 目标项目是否有自己的 `AGENTS.md` / `CODEX.md`
- `CODEX_REVIEWER_FRAMEWORK_ROOT` 是否还指向旧项目

### 3. 想把 wrapper 做成跨项目共享

当前设计本来就是全局共享 wrapper：

- 共享脚本放在 `~/.codex/scripts/` 或 `%USERPROFILE%\.codex\scripts\`
- 项目规范仍留在项目自己的 `AGENTS.md` / `CODEX.md`
- reviewer 的运行产物仍写回项目本地 `.codex/`

### 4. `codex mcp` 和脚本生成结果不一致

这是预期内的风险点。

- `codex mcp` 是静态参考片段
- 真正用于迁移的 source of truth 是 `setting.sh` 和 `setting.ps1`

如果两者不一致，优先相信迁移脚本。

## 迁移完成后的目录分工

迁移后可以按下面的分工理解整套 workflow：

- 仓库负责维护规范、wrapper 源码、skill 源码和迁移脚本
- 用户目录 `~/.codex/` 或 `%USERPROFILE%\.codex\` 负责承载全局配置、共享 wrapper 和共享 skills
- 具体项目负责承载 `AGENTS.md`、`CODEX.md` 和运行产物 `.codex/`
