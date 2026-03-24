---
name: multi-codex-orchestrator
description: Use when the user asks for codex-reviewer, 审查 Codex, multi-codex, reviewer-assisted implementation, or wants code generation paired with an MCP-backed review pass.
---

# Multi-Codex Orchestrator

Use this skill when the current Codex instance is the main executor and must coordinate with an MCP-backed reviewer.

## Required Flow

1. Read `./.codex/CODEX.md` first if it exists.
2. If reviewer assistance is requested or required, start the reviewer with `mcp__codex_reviewer__codex`.
3. In the first reviewer prompt:
   - Put a unique `task_marker` on line 1.
   - Include the exact text `$codex-reviewer-workflow`.
   - Tell the reviewer to read `./.codex/AGENTS.md` first and `./.codex/CODEX.md` second if they exist.
   - State the task type and the artifact path under `./.codex/`.
4. Keep implementation, validation, and final product decisions in the main Codex session unless project rules explicitly say otherwise.
5. After coding and local validation, send a follow-up review request with `mcp__codex_reviewer__codex_reply` using the `structuredContent.conversation_id` returned by the first reviewer call.
6. If the `codex-reviewer` MCP tool is unavailable, say so clearly and continue with a local review instead of silently skipping the review step.

## Initial Reviewer Prompt Template

```text
[TASK_MARKER: 20260323-120000-ABCD]
$codex-reviewer-workflow
You are the reviewer Codex for this repository.
Read ./.codex/AGENTS.md first and ./.codex/CODEX.md second if they exist before doing any substantive work.

Task type: context scan | complex design | code review
Goal:
- ...
Scope:
- ...

Output requirements:
1. Write ./.codex/context-initial.json or ./.codex/review-report.md as appropriate.
2. Use the MCP tool response's structuredContent.conversation_id as the canonical conversation id.
```

## Review Handoff Checklist

- Include the changed file list.
- Include acceptance criteria.
- Include validation commands already run.
- Include focus areas and non-negotiable risks.
- Read the reviewer artifact before making the final decision.
