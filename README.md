# Codex Reviewer Multi-Agent Framework

基于 Codex 的“主 Codex 编写 + 审查 Codex 审核”协作框架。
## 内容

```text
.
├── .codex/                              # 项目内协作规范与 reviewer 产物目录
│   ├── AGENTS.md                        # 审查 Codex 操作手册
│   └── CODEX.md                         # 主 Codex 协作规范
├── base/
│   ├── codex-latest                     # macOS / Linux reviewer launcher
│   └── codex-latest.cmd                 # Windows reviewer launcher
├── scripts/
│   └── codex_reviewer_mcp.py            # reviewer wrapper，仓库内 source of truth
├── skills/
│   ├── codex-reviewer-workflow/         # 审查 Codex 固定工作流 skill
│   └── multi-codex-orchestrator/        # 主 Codex 编排 skill
├── tests/
│   ├── smoke_test_codex_reviewer_mcp.py # wrapper 端到端 smoke test
│   └── test_codex_reviewer_wrapper.py   # wrapper 单元测试
├── setting.ps1                          # Windows 初始化脚本
├── setting.sh                           # macOS / Linux 初始化脚本
└── README.md
```

运行过程中，目标项目本地还会使用这些 `.codex/` 产物：

- `.codex/context-initial.json`：首次上下文扫描结果
- `.codex/review-report.md`：最终审查报告
- `.codex/reviewer-jobs/`：异步 reviewer job 状态与诊断信息
- `.codex/codex-reviewer-sessions.json`：`task_marker -> conversation_id` 会话映射

## 初始化

### 前置依赖

- `conda`
- `npx`
- `python3` 或 `python`
- 已安装可用的 Codex CLI / VS Code Codex Plugin / Codex app(macOS only)

### Windows

```powershell
./setting.ps1
```

### macOS / Linux

```bash
bash ./setting.sh
```

### 可选环境变量

初始化脚本支持这些覆盖项：

- `CODEX_HOME`
- `CODEX_BINARY`
- `CODEX_MODEL`
- `CODEX_REASONING_EFFORT`
- `CODEX_REVIEWER_FRAMEWORK_ROOT`
- `CODEX_REVIEWER_LOG_PATH`
- `CODEX_REVIEWER_CLEANUP_MODE`
- `ENABLE_EXA`
- `EXA_API_KEY`
- `SHRIMP_DATA_DIR`
- `CONDA_EXE`：仅 `setting.ps1` 使用



### 初始化脚本会做什么

初始化脚本会把当前仓库内容迁移到全局 `~/.codex/`：

- 生成 `~/.codex/config.toml`

- 复制 `base/codex-latest*` 到 `~/.codex/bin/`

- 复制 `scripts/` 到 `~/.codex/scripts/`

- 复制 `skills/` 到 `~/.codex/skills/`

- 注册 `codex-reviewer`、`sequential-thinking`、`shrimp-task-manager`、`chrome-devtools`


通常不需要为每个项目反复改全局配置。需要接入 reviewer 的项目只要准备自己的项目内 `.codex/` 即可；跨仓库使用时，把 MCP 调用的 `cwd` 指向目标仓库。

## 使用方式
在Codex CLI / VSCode Codex Plugin / Codex app(macOS only)中输入
```
/skill Mult-Codex-Orchestrator <你的指令>
```


推荐工作流来自项目内 [CODEX.md](.codex/CODEX.md) 与 [AGENTS.md](.codex/AGENTS.md)：

1. 主 Codex 先调用 `mcp__codex_reviewer__codex`
2. 如果首次只返回 `job_id` 或 `conversation_id = null`，轮询 `mcp__codex_reviewer__review_status`
3. 拿到 `conversation_id` 和 / 或 reviewer 产物后，继续主流程编码
4. 编码后调用 `mcp__codex_reviewer__codex_reply`
5. 再次轮询 `review_status`
6. 最后调用 `mcp__codex_reviewer__review_gate`







## 自动检查行为

`setting.sh` / `setting.ps1` 在复制完脚本和配置后，会自动执行一次：

```bash
python3 ~/.codex/scripts/codex_reviewer_mcp.py doctor --cwd <project_root>
```

如果你设置了：

```bash
CODEX_REVIEWER_CLEANUP_MODE=reviewer
```

初始化脚本还会额外运行 stale reviewer cleanup。默认值是 `none`，只诊断不清理。

## 验证命令

仓库内当前的本地验证入口：

```bash
python3 -m py_compile scripts/codex_reviewer_mcp.py tests/test_codex_reviewer_wrapper.py tests/smoke_test_codex_reviewer_mcp.py
python3 -B -m unittest tests/test_codex_reviewer_wrapper.py
python3 -B tests/smoke_test_codex_reviewer_mcp.py
python3 -B scripts/codex_reviewer_mcp.py probe --json
```

这些测试覆盖了：

- MCP `initialize -> notifications/initialized -> tools/list`
- 异步 `codex` / `codex_reply` job 排队与复用
- `review_status` 查询优先级
- `task_marker` 规范化
- startup janitor 对 `stale` / `failed` job 的处理
- `review_gate` 的本地与 MCP 收口逻辑


## 已知行为

根据当前仓库实现和实测结果，可以把这套系统视为“可用、可迁移的异步 reviewer 工作流”，但要注意它不是旧式同步 reviewer：

- 首次 `codex` 调用可能先返回 `job_id`
- `conversation_id` 可能稍后才由 `review_status` 补齐
- 在远端 websocket 不稳定时，首轮 job 可能记成 `timeout`，但 artifact 和 `conversation_id` 仍可能已经落地
- 因此调用方必须依赖 `review_status` 和 `review_gate`，不能只看首次调用结果

## 常见问题

-  当前VSCode 存在多个版本的Codex Plugin，会导致设置冲突

### Linux/Macos
```shell
% agentframework % ls -1 ~/.vscode/extensions | rg -i openai
```
### Win
```powershell
Get-ChildItem "$env:USERPROFILE\.vscode\extensions" |
Where-Object { $_.Name -match 'openai' } |
Select-Object Name
```
如果存在两个以上的Plugin版本，删除其中一个并重启VSCode