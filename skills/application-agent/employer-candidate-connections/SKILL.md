---
name: employer-candidate-connections
description: Research employer products, customers and optional activities to find authentic, candidate-confirmed connections for a specific cover letter.
---

# Employer-to-Candidate Connection Discovery

Activate after job and company research, before drafting a tailored cover letter.

1. Research only relevant employer facts (products, users, market, publicly documented employee activities) through authorized research tools. Keep URLs. One employee activity is not proof of universal company culture.
2. Look for supported intersections with candidate profile `EXP-*` and optional `CTX-*` records, including older careers, product-user familiarity, hobbies and volunteering.
3. If no confirmed connection is recorded, ask **one** optional, specific, nonleading question; e.g., "Have you used the employer's products in your own projects?" Never assume yes. A hobby should not automatically become a cover-letter theme.
4. Ask the candidate separately whether they want that specific detail disclosed in **this cover letter**. Profile ownership or candidate-reported facts are not permission to publish. If the candidate says no, does not answer, or cannot be reached, omit and proceed.
5. Record the company evidence, confirmed candidate fact, response to the disclosure request, scope and connection rationale in the application-specific `CANDIDATE_CONNECTIONS.json` when useful. See `docs/CANDIDATE_CONTEXT.md`. `use_decision=include` requires candidate confirmation and explicit approval. Do not fabricate a consent reference.
6. Select at most a few high-value connections. Favor firsthand product/customer perspective where job-relevant; avoid contrived cultural flattery, personal disclosures or replacing technical qualifications with hobbies.
7. Route proposed *permanent* candidate profile updates through the Orchestrator and Candidate Profile Development Agent, never write `candidate/context/` yourself. Keep unanswered discovery optional and nonblocking.

Do not infer candidate experience from public employer material, or infer engineering capability from a product purchase. Both independent reviewers should examine any included personal connection.
