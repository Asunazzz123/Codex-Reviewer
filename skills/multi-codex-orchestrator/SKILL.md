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
5. If the first reviewer call returns only a `job_id` or a null `conversation_id`, poll `mcp__codex_reviewer__review_status` until the `conversation_id` and/or required artifact is ready.
6. After coding and local validation, send a follow-up review request with `mcp__codex_reviewer__codex_reply` using the `conversation_id` returned directly by the first call or later by `review_status`.
7. Poll `mcp__codex_reviewer__review_status` again after `codex_reply` until `./.codex/review-report.md` is ready or the job reaches a terminal state.
8. Before final delivery, call `mcp__codex_reviewer__review_gate` and do not finish unless it reports `gate_passed=true`.
9. If the `codex-reviewer` MCP tool is unavailable, say so clearly, switch to a local reviewer fallback, and still block completion until `./.codex/review-report.md` exists and the equivalent local review gate passes.

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
2. If the first call returns `job_id` before `conversation_id`, use `review_status` polling to obtain the canonical conversation id.
```

## Review Handoff Checklist

- Include the changed file list.
- Include the reviewer `job_id` when the async wrapper returns one.
- Include acceptance criteria.
- Include validation commands already run.
- Include focus areas and non-negotiable risks.
- Poll `review_status` instead of assuming the first reviewer call will synchronously finish.
- Read the reviewer artifact before making the final decision.
- Run the reviewer gate check after the final review step and treat it as a completion blocker.
