---
name: rag-architecture-assessment
description: Evaluate technical claims about RAG data ingestion, retrieval and grounded
  generation. Use when a candidate cites embeddings, vector search or RAG systems.
---

# Rag Architecture Assessment

## When to activate

Use when a candidate cites embeddings, vector search or RAG systems.

## Procedure

1. Trace ingestion and chunking stages.
2. Identify embeddings, storage and metadata strategy.
3. Examine retrieval, filtering, reranking and context selection if implemented.
4. Check generation grounding and failure behavior.
5. Assess evaluation methodology and metrics only when documented.
6. Separate implemented from planned and uncertain components.

## Expected output

A RAG component and evidence assessment.

## Domain-specific caution

Vector search alone does not establish a complete evaluated RAG architecture.

## Standing safeguards

- Follow the invoking agent's AGENTS.md and actual tool/filesystem permissions. This skill does not grant new powers.
- Respect human-in-the-loop approval for all configured gated tools. For authoritative profile writes, obtain explicit candidate approval independently of tool approval.
- Preserve stable EXP-, PROJECT-, ACC-, SKILL-, EDU-, CERT-, EVID- and FUP- identifiers and link statements to the relevant records.
- Do not invent jobs, qualifications, scope, technology usage, quantifiable results, evidence or completion status.
- Open FUP records do not by themselves block producing an otherwise supported resume or cover letter.

## Scope

Intended agent group: **Technical Reviewer Agent**. For other agents, use only if their AGENTS.md explicitly permits the operation; route disallowed work to the owner.
