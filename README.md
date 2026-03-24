# Codex Reviewer 迁移文档
Codex 代码编写-代码审核 multi-agent, 参考[Claude Code+Codex](https://linux.do/t/topic/1003435?u=zhongruan)改造而成
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
全局配置不需要手动修改，需要代码审核的项目需要**手动迁移** 项目内的`.codex`
