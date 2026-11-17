---
name: tool-use-and-approval-compliance
description: Choose authorized tools and honor separate human-in-the-loop and candidate-consent
  requirements. Use before file reads or writes, external web research, deletions
  and candidate-profile changes.
---

# Tool Use And Approval Compliance

## When to activate

Use before file reads or writes, external web research, deletions and candidate-profile changes.

## Procedure

1. Identify the tool, target and side effects.
2. Check current AGENTS.md, middleware and filesystem permissions.
3. Request and obtain any required tool-level approval.
4. For authoritative profile changes additionally obtain explicit candidate consent.
5. Perform only the approved action.
6. Verify the resulting file or tool output.
7. Report denied, incomplete or failed actions.

## Expected output

A verified action record with approval and outcome, or an explicit blocker.

## Domain-specific caution

Tool approval never substitutes for candidate consent; a skill grants no tool access.

## Standing safeguards

- Follow the invoking agent's AGENTS.md and actual tool/filesystem permissions. This skill does not grant new powers.
- Respect human-in-the-loop approval for all configured gated tools. For authoritative profile writes, obtain explicit candidate approval independently of tool approval.
- Preserve stable EXP-, PROJECT-, ACC-, SKILL-, EDU-, CERT-, EVID- and FUP- identifiers and link statements to the relevant records.
- Do not invent jobs, qualifications, scope, technology usage, quantifiable results, evidence or completion status.
- Open FUP records do not by themselves block producing an otherwise supported resume or cover letter.

## Scope

Intended agent group: **Shared by every agent**. For other agents, use only if their AGENTS.md explicitly permits the operation; route disallowed work to the owner.
