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
