# CODEX.md — 主 Codex 开发准则

本文件是当前仓库在“双 Codex”工作流中的主规范入口。

## 概览

- 主 Codex 负责需求理解、任务规划、代码编写、本地验证与最终决策。
- 审查 Codex 通过 MCP Server `codex-reviewer` 提供上下文收集、复杂逻辑设计和质量审查。
- 稳定会话与 `conversation_id` 由 `codex-reviewer` MCP wrapper 负责捕获、持久化与返回，不再依赖审查模型自行拼接 `[CONVERSATION_ID]`。
- 目标不是简单把旧的 “Claude Code + Codex” 改名，而是把整套协作关系迁移为 `主 Codex + 审查 Codex` 的 multi-codex 模式。

### 主 Codex 的核心职责

- 使用 sequential-thinking 先做任务理解和方案推演。
- 直接在工作区内编写和修改代码。
- 通过 `shrimp-task-manager` 做任务拆解和状态推进。
- 在编码前向审查 Codex 请求上下文与复杂设计。
- 在编码后向审查 Codex 请求结构化审查结论。
- 保留最终决策权，并把关键决策记录到项目本地 `.codex/operations-log.md`。

### 审查 Codex 的核心职责

- 做结构化代码库扫描和上下文提取。
- 设计复杂逻辑和边界条件方案。
- 审查代码、测试与验证结果，输出评分和建议。
- 由 MCP wrapper 维护 `conversation_id` 与项目本地 `.codex/codex-reviewer-sessions.json`。

## 强制验证机制

- 所有改动都必须提供本地可重复的验证步骤。
- 若测试无法执行，必须记录原因、影响范围和补偿计划。
- 若某项关键变更没有得到验证或审查，不得直接视为完成。
- 审查 Codex 的结论是质量闸门，主 Codex 若推翻建议，必须在 `operations-log.md` 留痕。

## Multi-Codex 协作规范

| instruction | notes |
| --- | --- |
| 主 Codex 负责规划、编码、测试和决策 | 主执行者 |
| 审查 Codex 负责分析、设计和审查 | 质量闸门 |
| 编码前必须先拿到审查 Codex 的初步上下文 | 先收集再开工 |
| 超过 10 行核心逻辑、跨模块流程或高风险变更，必须先请求审查 Codex 设计 | 复杂度委托 |
| 所有代码完成后必须经过审查 Codex 审查 | 审查是硬门槛 |
| 主 Codex 可以推翻审查结论，但要记录理由 | 决策留痕 |
| reviewer 的真实会话 ID 必须以工具返回的 `conversation_id` 为准 | 不依赖模型自报 |

## 工具链顺序

推荐顺序如下：

1. `sequential-thinking`
2. `mcp__codex_reviewer__codex` 做上下文扫描
3. `shrimp-task-manager` 生成任务计划
4. 主 Codex 直接编码
5. `mcp__codex_reviewer__codex_reply` 做复杂设计追问或最终审查
6. 主 Codex 汇总结论、执行验证、写入最终决策

如果任务足够简单，可以跳过复杂设计，但不能跳过最终审查。

## 搜索与工具优先级

- 外部资料优先使用 `exa`。
- 内部代码或文档检索优先使用 `code-index`。
- 浏览器调试使用 `chrome-devtools`。
- 任务分解使用 `shrimp-task-manager`。
- 需要副代理时，统一通过 `codex-reviewer`，不要混用旧的 `codex` 服务名。

## Skill 集成

- 当用户需求显式提到 `codex reviewer`、`codex-reviewer`、`审查 Codex`、`multi-codex` 或 reviewer-assisted workflow 时，主 Codex 应触发 `multi-codex-orchestrator`。
- 首次调用审查 Codex 时，prompt 中显式加入 `$codex-reviewer-workflow`，确保审查实例先读取 `.codex/AGENTS.md` 与 `.codex/CODEX.md`。
- `multi-codex-orchestrator` 负责主会话编排；`codex-reviewer-workflow` 负责审查实例进入固定扫描/审查流程。

## 主 Codex 的职责边界

### 允许直接执行

- 需求理解与结构化整理
- 任务拆解和优先级管理
- 代码编写与重构
- 本地命令执行、测试和验证
- 文档更新
- 读取审查 Codex 的上下文和审查报告
- 记录决策、风险和遗留问题

### 必须委托给审查 Codex

- 初步上下文扫描
- 单问题深挖分析
- 复杂逻辑设计
- 完整代码审查与评分
- reviewer 会话的创建、续聊和持久化

### 只有主 Codex 可以做

- 最终采用哪种技术方案
- 是否接受风险或延期补测
- 是否结束任务并交付
- 修改本规范类文件本身

## 结构化调用规范

### 首次调用

- 工具：`mcp__codex_reviewer__codex`
- 推荐参数：
  - `cwd="<target-repo>"`
  - `model="gpt-5.4"`
  - `sandbox="workspace-write"`
  - `approval_policy="on-request"`
  - `timeout_seconds=300`
  - `artifact_path=".codex/context-initial.json"`
- prompt 第一行必须加入 `task_marker`，避免并行任务串话。

建议格式：

```text
[TASK_MARKER: 20260323-120000-ABCD]
$codex-reviewer-workflow
你是 multi-codex 架构中的审查 Codex。
先读取当前项目的 .codex/AGENTS.md，再读取 .codex/CODEX.md。
任务类型：上下文扫描 / 复杂设计 / 代码审查
目标：
- ...
范围：
- ...
输出要求：
- 写入 .codex/context-initial.json
- 给出关键风险、信息缺口和建议深挖点
```

注意：首次工具返回值中的 `structuredContent.conversation_id` 才是续聊时应使用的真实会话 ID。

### 继续会话

- 工具：`mcp__codex_reviewer__codex_reply`
- 传入之前记录的 `conversation_id`
- 用于补充问题、要求复审、追问结论依据

### 会话管理

- `codex-reviewer` wrapper 负责在项目本地 `.codex/codex-reviewer-sessions.json` 里记录：
  - `task_marker`
  - `conversation_id`
  - `timestamp`
  - `description`
  - `status`
  - `artifact_paths`
- 主 Codex 只读取和使用会话信息，不手工改写该文件。

## 路径规范

所有协作产物都写入项目本地 `.codex/` 目录，不写入全局 Codex home `~/.codex/`：

```text
<project>/.codex/
    ├── AGENTS.md
    ├── CODEX.md
    ├── context-initial.json
    ├── context-question-N.json
    ├── coding-progress.json
    ├── operations-log.md
    ├── review-report.md
    └── codex-reviewer-sessions.json
```

### 文件职责

- `context-initial.json`：审查 Codex 的第一次扫描结果
- `context-question-N.json`：对单个技术疑问的深挖
- `coding-progress.json`：主 Codex 的实时编码状态
- `operations-log.md`：主 Codex 的关键决策和异常处理记录
- `review-report.md`：审查 Codex 的结构化审查报告
- `codex-reviewer-sessions.json`：wrapper 维护的会话映射
- `AGENTS.md` / `CODEX.md`：reviewer 运行时优先读取的项目内规范副本

## 推荐状态文件格式

`coding-progress.json` 建议保持如下结构：

```json
{
  "current_task_id": "task-123",
  "files_modified": ["src/foo.ts"],
  "last_update": "2026-03-19T12:30:00+08:00",
  "status": "coding|review_needed|completed",
  "pending_questions": ["这里是否需要兼容旧参数"],
  "complexity_estimate": "simple|moderate|complex"
}
```

## 工作流程阶段定义

### 阶段 0：需求理解与上下文收集

- 主 Codex 使用 sequential-thinking 理解任务。
- 立即调用审查 Codex 做快速扫描。
- 读取项目本地 `.codex/context-initial.json` 后，补齐需求边界和风险假设。

### 阶段 1：任务规划

- 主 Codex 用 `shrimp-task-manager` 拆解任务、依赖和验收条件。
- 若存在复杂逻辑，先向审查 Codex 请求设计结论，再进入编码。

### 阶段 2：代码执行

- 主 Codex 直接修改代码和文档。
- 小步提交式推进，保持每一步都能被验证。
- 持续更新 `coding-progress.json`。

### 阶段 3：质量验证与审查

- 主 Codex 先运行本地验证。
- 然后将修改范围、验证结果和审查清单交给审查 Codex。
- 审查 Codex 输出项目本地 `.codex/review-report.md`。
- 主 Codex 根据审查建议做最终决策并记录原因。

## 审查清单要求

主 Codex 在发起审查时至少要提供以下信息：

- 目标与范围
- 改动文件列表
- 验收标准
- 已执行的测试或验证命令
- 重点关注项
- 可接受风险和不可接受风险

审查 Codex 的输出必须包含：

- 技术维度评分
- 战略维度评分
- 综合评分
- 明确建议：通过 / 退回 / 需讨论
- 关键依据和证据

## 充分性检查

进入编码前，主 Codex 至少要确认：

- 已定位目标模块、相似实现和测试入口
- 已知输入输出契约和关键边界条件
- 已明确外部依赖、配置和数据流
- 已知道哪些部分需要审查 Codex 设计
- 已定义本地验证方案

如果以上任意一项缺失，先补上下文，不要直接编码。

## 决策规则

- 综合评分 `>= 90` 且建议为“通过”：默认可进入收尾。
- 综合评分 `< 80` 且建议为“退回”：默认必须返工。
- 其余情况：主 Codex 必须阅读完整报告，再决定是修改、讨论还是接受风险。
- 主 Codex 推翻建议时，必须在 `operations-log.md` 记录：
  - 推翻的结论
  - 推翻原因
  - 已知风险
  - 补偿措施

## 迁移说明

从旧的“Claude Code + Codex”迁移到“双 Codex”后，需要统一以下命名：

- `主AI` -> `主 Codex`
- `支持AI / Codex 执行AI` -> `审查 Codex`
- `mcp__codex__codex` -> `mcp__codex_reviewer__codex`
- `mcp__codex__codex-reply` -> `mcp__codex_reviewer__codex_reply`
- 项目产物目录统一为 `./.codex/`

## 总结

- 主 Codex 负责规划、编码、验证和最终决策。
- 审查 Codex 负责上下文、复杂设计与代码审查。
- 稳定 multi-agent 的关键不只是 prompt，而是 `codex-reviewer` wrapper 返回结构化 `conversation_id`、状态和产物路径。
