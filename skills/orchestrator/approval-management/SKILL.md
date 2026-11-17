---
name: approval-management
description: Coordinate approvals for tool calls, profile changes and consequential
  candidate-facing actions. Use when a requested task needs human approval or may
  change authoritative information.
---

# Approval Management

## When to activate

Use when a requested task needs human approval or may change authoritative information.

## Procedure

1. Identify exact target, proposed operation and risk.
2. Determine candidate-consent and tool-approval requirements separately.
3. Present a specific before-and-after summary for profile changes.
4. Wait for explicit scoped consent before authorizing the Profile Agent.
5. Obtain any required runtime tool approval.
6. Verify writes and log decisions.

## Expected output

An approval log with scope, decision and verified action.

## Domain-specific caution

Silence and tool-level permission never constitute candidate consent.

## Standing safeguards

- Follow the invoking agent's AGENTS.md and actual tool/filesystem permissions. This skill does not grant new powers.
- Respect human-in-the-loop approval for all configured gated tools. For authoritative profile writes, obtain explicit candidate approval independently of tool approval.
- Preserve stable EXP-, PROJECT-, ACC-, SKILL-, EDU-, CERT-, EVID- and FUP- identifiers and link statements to the relevant records.
- Do not invent jobs, qualifications, scope, technology usage, quantifiable results, evidence or completion status.
- Open FUP records do not by themselves block producing an otherwise supported resume or cover letter.

## Scope

Intended agent group: **Orchestrator / Career Agent**. For other agents, use only if their AGENTS.md explicitly permits the operation; route disallowed work to the owner.
