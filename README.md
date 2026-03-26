# Codex Reviewer 迁移文档
Codex 代码编写-代码审核 multi-agent, 参考[Claude Code+Codex协作开发文档](https://linux.do/t/topic/1003435?u=zhongruan)改造而成
## 内容
```plaintext
.
├── .codex                           // 主agent与代码审核sub-agent的prompt
│   ├── AGENTS.md
│   └── CODEX.md
├── .scripts                         // 工具脚本，初始化后会迁移至~/.codex/scripts
│   └── codex_reviewer_mcp.py
├── base                             // 审核agent的校验器，初始化后会迁移至~/.codex/bin
│   ├── codex-latest
│   └── codex-latest.cmd
├── README.md
├── setting.ps1                      // windows的初始化脚本, 写入MCP服务器并迁移文件
├── setting.sh                       // Linux/Macos的初始化脚本, 写入MCP服务器并迁移文件
└── skills                           // mulit-agent SKILLS
    ├── codex-reviewer-workflow
    └── multi-codex-orchestrator


```

## 初始化
Windows
```powershell
./setting.ps1
```

Linux/Macosx
```
bash ./setting.sh
```
全局配置通常不需要随项目切换而修改。需要代码审核的项目只需要准备自己的 `.codex`；跨仓库调用 reviewer 时把 `cwd` 指向目标仓库即可。只有 reviewer framework 仓库位置发生变化时，才需要更新 `CODEX_REVIEWER_FRAMEWORK_ROOT`。

## 部署后健康检查

- `setting.sh` / `setting.ps1` 现在会在复制配置与脚本后自动运行 `codex_reviewer_mcp.py doctor`。
- wrapper 当前按 `2025-06-18` 协议响应 `initialize`，用于对齐当前 Codex 的 MCP 握手行为。
- `doctor` 会检查 wrapper 健康状态、多个 `openai.chatgpt-*` 扩展版本并存、是否需要重启 VS Code/Codex 宿主，以及是否存在可清理的 stale reviewer wrapper 进程。
- `probe --json` 会额外执行一遍本地 MCP 握手链路：`initialize -> notifications/initialized -> tools/list`。当你怀疑 “手工跑脚本没问题，但 Codex 还是注册不出工具” 时，优先先跑 `probe`。
- 如果需要在安装后顺手清理陈旧 reviewer 进程，可设置 `CODEX_REVIEWER_CLEANUP_MODE=reviewer`；默认值是 `none`，只诊断不清理。
- 如果需要抓取握手级别日志，可在运行 `setting.sh` / `setting.ps1` 之前导出 `CODEX_REVIEWER_LOG_PATH`。迁移脚本会把该变量写入 `codex-reviewer` 的 MCP env，但默认不会开启日志。
- 默认不会自动结束 `codex app-server` 宿主进程；如果 `doctor` 提示宿主早于最新 `config.toml` / wrapper 更新，请完全重启 VS Code 或 Codex.app。

## Reviewer Gate

- reviewer wrapper 现在同时提供 MCP 工具 `review_gate`，以及 CLI 子命令 `review-gate`、`doctor`、`probe`、`cleanup`。
- 主 Codex 在最终交付前必须确认 reviewer gate 通过；如果 `codex-reviewer` MCP 不可用，可以降级到本地 reviewer，但仍必须产出 `./.codex/review-report.md` 后才能完成任务。

## 推荐排障顺序

1. 重新运行 `setting.sh` 或 `setting.ps1`，确保全局 `~/.codex/scripts/codex_reviewer_mcp.py` 与最新仓库脚本一致。
2. 完全重启 VS Code / Codex 宿主，避免复用旧的 `app-server`。
3. 手工执行 `codex_reviewer_mcp.py probe --json`，确认本地 wrapper 在 `2025-06-18` 协议下能完成 `tools/list`。
4. 如果 `probe` 成功但 Codex 仍未注册出 `mcp__codex_reviewer__*`，再检查 `CODEX_REVIEWER_LOG_PATH` 指向的日志，确认宿主是否真的打到了 `initialize`。

这个顺序是根据这些上游 issue 总结出来的：

- [openai/codex#14933](https://github.com/openai/codex/issues/14933)：手工 `initialize` 成功，不代表 Codex 宿主一定能真正使用该 MCP。
- [openai/codex#5677](https://github.com/openai/codex/issues/5677)：新客户端会以 `2025-06-18` 握手，旧协议响应可能在 `tools/list` 前后触发失败。
- [openai/codex#5671](https://github.com/openai/codex/issues/5671)：stdio MCP 在当前 Codex / rmcp 组合下对协议与 framing 更敏感，诊断日志要避免污染 `stdout`。

## 维护说明

- `.scripts/codex_reviewer_mcp.py` 是仓库内的 source of truth。
- 若保留仓库根目录的 `codex_reviewer_mcp.py` 镜像文件，请在修改 `.scripts` 后同步它，避免阅读或手工调试时拿错版本。
