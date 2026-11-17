---
name: actionable-review-reporting
description: Write a recruiter review report with exact locations, severity, rationales
  and edits the writer can apply. Use when returning review results to Application
  Development Agent.
---

# Actionable Review Reporting

## When to activate

Use when returning review results to Application Development Agent.

## Procedure

1. State scope and material reviewed.
2. Give overall decision and important limitations.
3. For each finding cite exact section/bullet.
4. Classify critical, high, medium or low.
5. Explain screening impact.
6. Suggest a concrete evidence-backed correction.
7. Indicate whether re-review is needed.

## Expected output

A structured, actionable reviewer report.

## Domain-specific caution

Vague advice without a concrete change is not sufficient.

## Standing safeguards

- Follow the invoking agent's AGENTS.md and actual tool/filesystem permissions. This skill does not grant new powers.
- Respect human-in-the-loop approval for all configured gated tools. For authoritative profile writes, obtain explicit candidate approval independently of tool approval.
- Preserve stable EXP-, PROJECT-, ACC-, SKILL-, EDU-, CERT-, EVID- and FUP- identifiers and link statements to the relevant records.
- Do not invent jobs, qualifications, scope, technology usage, quantifiable results, evidence or completion status.
- Open FUP records do not by themselves block producing an otherwise supported resume or cover letter.

## Scope

Intended agent group: **Recruiting and ATS Reviewing Agent**. For other agents, use only if their AGENTS.md explicitly permits the operation; route disallowed work to the owner.
