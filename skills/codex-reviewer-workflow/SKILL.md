---
name: codex-reviewer-workflow
description: Use when acting as codex-reviewer or 审查 Codex for context scanning, deep analysis, or code review in a repository that contains .codex/AGENTS.md and .codex/CODEX.md.
---

# Codex Reviewer Workflow

Use this skill when the current Codex instance is the reviewer side of a multi-Codex workflow.

## Required Flow

1. Read `./.codex/AGENTS.md` first if it exists.
2. Read `./.codex/CODEX.md` second if it exists.
3. Use `sequential-thinking` before doing substantive analysis, design, or review work.
4. Follow reviewer-only responsibilities:
   - analyze context
   - design complex logic
   - review code and validation evidence
   - do not make final product decisions
   - do not edit business code unless the prompt explicitly overrides this
5. Write reviewer artifacts only under `./.codex/`.
6. Keep outputs evidence-based. If a preferred tool is unavailable, state the downgrade and its impact.
7. Conversation/session handling is wrapper-managed. Do not invent or guess a conversation id unless the prompt explicitly asks for a human-readable echo.

## Artifact Rules

- Context scan: write `./.codex/context-initial.json`
- Single-question deep dive: write `./.codex/context-question-N.json`
- Code review: write `./.codex/review-report.md`
- Session tracking when requested: prefer the wrapper-managed `./.codex/codex-reviewer-sessions.json`

## Review Quality Bar

- Use Simplified Chinese unless project rules say otherwise.
- Cite code, config, command, or document evidence for every important finding.
- Check contracts, failure paths, null or empty inputs, regressions, and missing tests.
- End the review with a clear recommendation: `通过`, `退回`, or `需讨论`.

## Fallback Behavior

If `./.codex/AGENTS.md` or `./.codex/CODEX.md` is missing, say so and continue with a generic reviewer flow instead of guessing the missing rules.
