---
name: profile-change-control
description: Apply candidate-authorized changes to the authoritative profile with
  traceability and verification. Use when a record correction, addition or deletion
  is proposed.
---

# Profile Change Control

## When to activate

Use when a record correction, addition or deletion is proposed.

## Procedure

1. Inspect current state and related IDs.
2. Prepare a minimal, explicit change proposal.
3. Identify source and potential downstream impact.
4. Seek explicit candidate approval for this exact change.
5. Obtain separate tool approval as configured.
6. Write only within authorized paths.
7. Verify resulting content and report updated IDs.

## Expected output

A before/after change record with both approvals and verification.

## Domain-specific caution

The Profile Agent is the only delegated profile maintainer.

## Standing safeguards

- Follow the invoking agent's AGENTS.md and actual tool/filesystem permissions. This skill does not grant new powers.
- Respect human-in-the-loop approval for all configured gated tools. For authoritative profile writes, obtain explicit candidate approval independently of tool approval.
- Preserve stable EXP-, PROJECT-, ACC-, SKILL-, EDU-, CERT-, EVID- and FUP- identifiers and link statements to the relevant records.
- Do not invent jobs, qualifications, scope, technology usage, quantifiable results, evidence or completion status.
- Open FUP records do not by themselves block producing an otherwise supported resume or cover letter.

## Scope

Intended agent group: **Candidate Profile Development Agent**. For other agents, use only if their AGENTS.md explicitly permits the operation; route disallowed work to the owner.
