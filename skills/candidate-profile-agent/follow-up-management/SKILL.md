---
name: follow-up-management
description: Own persistent FUP records for unanswered questions, partial interviews
  and pending evidence. Use when information is missing, ambiguous, disputed or awaiting
  investigation.
---

# Follow Up Management

## When to activate

Use when information is missing, ambiguous, disputed or awaiting investigation.

## Procedure

1. Search for an existing matching FUP.
2. Assign an immutable FUP ID if new.
3. Record related IDs, source, known facts and unanswered questions.
4. Set status, priority, owner, evidence and next action.
5. Preserve partial answers and review updates.
6. Close resolved items or explicitly close unresolved with rationale.
7. Notify orchestrator of material changes.

## Expected output

An updated FUP register entry with audit trail.

## Domain-specific caution

Do not delete completed FUPs or turn questions into facts.

## Standing safeguards

- Follow the invoking agent's AGENTS.md and actual tool/filesystem permissions. This skill does not grant new powers.
- Respect human-in-the-loop approval for all configured gated tools. For authoritative profile writes, obtain explicit candidate approval independently of tool approval.
- Preserve stable EXP-, PROJECT-, ACC-, SKILL-, EDU-, CERT-, EVID- and FUP- identifiers and link statements to the relevant records.
- Do not invent jobs, qualifications, scope, technology usage, quantifiable results, evidence or completion status.
- Open FUP records do not by themselves block producing an otherwise supported resume or cover letter.

## Scope

Intended agent group: **Candidate Profile Development Agent**. For other agents, use only if their AGENTS.md explicitly permits the operation; route disallowed work to the owner.
