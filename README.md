# Career Agent

**A human-supervised, multi-agent system for creating evidence-based,
job-specific résumés and cover letters.**

Career Agent is a Python command-line prototype built with [LangChain
Deep Agents](https://www.langchain.com/deep-agents)
([GitHub](https://github.com/langchain-ai/deepagents)). It coordinates
specialist agents to develop a candidate profile, assess a job
posting, create application documents, and review them for
recruiting/ATS fit and technical accuracy. Its primary user is the
**job applicant**, not the employer.

The project targets applicants applying for AI engineering, backend
development, and related software roles. It runs locally with Ollama
by default and can be launched inside an existing Guix-based agent
container. See [GitHub:
`guix-terminal-jail`](https://github.com/sjholtz/guix-terminal-jail).

## Features

- **Five cooperating agents:** An orchestrator plus candidate-profile,
  application-development, recruiting/ATS-review, and technical-review
  specialists.
- **Candidate-centered evidence:** Profile material, evidence IDs, and
  follow-up (`FUP-`) records guide drafting. Unresolved follow-ups
  need not block documents based on already supported facts.
- **Human-in-the-loop controls:** Approval for research calls,
  application initialization, and permitted persistent
  writes. Candidate-profile changes have additional review safeguards.
- **Application workspace initialization:** Creates canonical
  company/job directories and provenance-bearing Markdown source
  records.
- **Job posting URLs as first-class inputs:** Accepts a public HTML
  posting URL **or** the complete posting text; saves extracted
  posting content in `job_description.md` after approval.
- **Research and document readers:** Local SearXNG search, bounded
  public HTML/PDF fetching, and local PDF reading.
- **LaTeX drafting workflow:** Instructions direct the application
  agent to use candidate-supplied résumé and cover letter templates;
  automatic verification and compilation are still planned.
- **Opt-in LangSmith tracing:** disabled by default for CLI runs.

## Architecture

| Agent | Responsibility | Persistent-file behavior |
| --- | --- | --- |
| **Orchestrator** | Coordinate the application workflow, delegation, decisions, and approvals | Restricted application writes require approval; cannot edit the candidate profile |
| **Candidate Profile Development** | Discover, refine, and track candidate experience and evidence | Candidate-profile changes require explicit candidate review and write approval |
| **Application Development** | Capture posting/company sources; map qualifications; draft résumé and cover letter | Can initialize a job directory only with approval; writes are restricted to approved application locations |
| **Recruiting & ATS Reviewer** | Review relevance, wording, screening considerations, and positioning in résumés and cover letters | Read-only reviewer |
| **Technical Reviewer** | Check technical claims, depth, consistency, and supporting evidence in résumé and cover letter | Read-only reviewer |

Role-specific instructions live in `agents/*/AGENTS.md`; shared
procedures live in `agents/shared/AGENTS.md`. System prompts are in
`prompts/` and reusable skills in `skills/`.

## Requirements

| Dependency | Requirement / purpose |
| --- | --- |
| **Python** | **3.13 or newer** (`pyproject.toml`) |
| **[uv](https://docs.astral.sh/uv/)** | Installs locked Python dependencies and runs the application |
| **[Ollama](https://ollama.com/)** | Default local model provider; configured model is `qwen3.5:9b` |
| **[SearXNG](https://docs.searxng.org/)** | Needed for the `web_search` tool; endpoint must be reachable from the agent environment |
| **LaTeX distribution + `latexmk`** | Optional for manually compiling output `.tex` files; the application does not invoke it automatically yet |
| **Guix agent launcher** | Optional, but recommended filesystem security; external to this repository; can use docker or other container |

Python dependencies are declared in [`pyproject.toml`](pyproject.toml)
and pinned in [`uv.lock`](uv.lock). Major packages include
`deepagents`, `langchain-ollama`, `langsmith`, `trafilatura`,
`pymupdf`, and `searxng-wrapper`. Development tools include `ruff` and
`ty`.

A running Ollama server and downloaded model are needed for private,
local, live agent responses. Research tools need network access; a
reachable SearXNG instance is required for search. The source code
does **not** include an Ollama server, SearXNG deployment, Guix
launcher, résumé templates, or personal candidate data.

## Installation

### 1. Get the code and dependencies

Clone this GitHub repository, then install the locked environment:

```bash
git clone https://github.com/sjholtz/resume-agent.git
cd resume-agent
uv sync --frozen
```

### 2. Start the configured model

Install [ollama](https://ollama.com/).

The default model is set in `models.py`. To run the default model,
once ollama has been installed, run:

```bash
ollama pull qwen3.5:9b
ollama serve   # only if Ollama is not running as a service
```

To use a different model or provider, update `models.py` (and any
provider-specific agent configuration in `agent.py`). The configured
model is shared by the orchestrator and specialists. You are
recommended to run a local model so that your data remains your own.

### 3. Configure web search

The search tool defaults to a local installation of
[SearXNG](https://docs.searxng.org/) found at
`http://localhost/searxng/`. Point it at a [reachable SearXNG
instance](https://searx.space/) if yours differs:

```bash
export SEARXNG_BASE_URL="http://localhost/searxng/"
```

Ensure SearXNG offers JSON search responses (see
[`search:formats`](https://docs.searxng.org/admin/settings/settings_search.html)).
Within a container, `localhost` refers to the container's networking
context, not automatically the host. External webpage/PDF readers and
the URL-only job snapshot use separate public-URL fetching controls.

### 4. Prepare a candidate workspace

Create a workspace for the agent **outside the source checkout**. For example:

```bash
mkdir -p ~/career-workspace/candidate ~/career-workspace/templates
```

Place your authoritative candidate profile and supporting evidence
under `candidate/`, and your existing résumé/cover-letter LaTeX
templates under `templates/`. The project does not supply candidate
records or templates. Confirm the agent can read those paths before
requesting an application.

The current graph factory (`agent.py`) explicitly uses its **process
working directory** as the candidate workspace. For a direct run,
launch from that workspace rather than from the code directory. The
`config.example.toml` file documents the reusable configuration
module, but its `candidate_root` option does **not** override the
graph factory's explicit CWD selection.

### 5. Run Career Agent

**Direct `uv` launch** (replace `~/bin/resume-agent` with the actual
source checkout path):

```bash
cd ~/career-workspace
uv run --project ~/bin/resume-agent --frozen \
  python ~/bin/resume-agent/main.py
```

**Optional Guix launcher** (only if you separately installed and
configured
[`guix-agent-jail`](https://github.com/sjholtz/guix-terminal-jail)):

```bash
guix-agent-jail --init ~/career-workspace
guix-agent-jail --app ~/bin/resume-agent ~/career-workspace
```

The Guix launcher is a separate project. Its `/app` and `/workspace`
mounts and network/model access must be configured separately (see
instructions at the `guix-terminal-jail` repo); the agent repository
does not install the launcher or its secrets/proxy setup.

**Single-message example:**

```bash
cd ~/career-workspace
uv run --project ~/bin/resume-agent --frozen \
  python ~/bin/resume-agent/main.py \
  --message "Describe your role and subagents without editing files."
```

The terminal UI supports interactive prompts, `/edit` for a multiline
message (using the editor you’ve configured via `$VISUAL` or `$EDITOR`
on your system), and `/quit` to exit. Other switches:

```text
--message "PROMPT"       Run one prompt
--debug                  Show tool/message diagnostics
--trace                  Enable LangSmith tracing for this run
--trace-project NAME     Set the LangSmith project (requires --trace)
--help                   Show CLI help
```

Tracing can transmit candidate information to the tracing
service. Keep it disabled unless you deliberately consent to that data
handling.

## Using the application workflow

Start by asking the Orchestrator to create your profile. Many rounds
of questions will allow you to enter all pertinent data about your
educational and occupational background. The Candidate Profile Agent
will store a candidate profile in the filesystem rooted where you
execute the agent (in `~/career-workspace` in the example above).

Then ask the Orchestrator to prepare an application, supplying the
**company**, **position title**, and **job description text or public
posting URL** (all must be present in the same prompt). The
Application Development Agent will request approval before running
`initialize_application` which will set up a filesystem for this
application. For URL-only input, the approved tool retrieves public
HTML and stores an extracted snapshot; it does not merely save the
link.

For example:

> Prepare a résumé and cover letter for **Example Co. — Backend
> Engineer** using the posting at
> **https://example.com/actual-job-posting**.

Replace the illustrative URL with a real, publicly accessible job
posting. The initializer accepts public HTML, not authenticated pages,
JavaScript-only postings, or PDF URLs. If extraction fails or the
saved text is incomplete, supply the full original posting text in
your prompt.

### Output locations

Agents use virtual paths rooted at `/workspace`; this maps to your
selected workspace directory (the path `~/career-workspace` in the
examples above).

```text
/workspace/
├── candidate/                       # authoritative candidate records
├── templates/                       # original LaTeX templates
└── applications/
    └── <company_slug>/
        ├── company_inf.md            # company-level source/provenance record
        ├── additional_inf.md         # reusable company-level additions
        └── <YYYY-MM-DD>-<position_slug>/
            ├── job_description.md    # captured description and provenance
            ├── additional_inf.md     # position-specific additions
            ├── qualification_matrix.md    # drafted later, if requested
            ├── resume.tex                  # drafted later, if requested
            └── cover_letter.tex            # drafted later, if requested
```

The initializer creates the company/job directories and four source
records ready for editing by the agents; missing optional
company/additional information is explicitly marked as not
provided. Other artifacts are generated later with separate write
approval. Existing source snapshots are retained rather than silently
overwritten. A URL-only retry with the same original URL reuses the
saved job-description snapshot.

The date and slugs above are generated by the initializer (lowercase
ASCII, with spaces/punctuation normalized).

### Approval and data-handling rules

- Research tools (`web_search`, `read_web_page`, `read_web_pdf`)
  require human approval; `initialize_application` separately requires
  approval **before** URL retrieval and source-record creation.
- Persistent application writes require approval and are confined to
  permitted application paths. Original templates cannot be changed
  through the built-in file tools.
- Ordinary file reads do not require approval. Agents can read files
  without asking for your permission.
- The application agent cannot edit the candidate profile or follow-up
  register. Candidate-profile changes have a dedicated review/approval
  path through the profile agent.
- The recruiting and technical review agents cannot write to the
  candidate workspace. They can only read the resulting documents and
  suggest corrections back to the application agent.

These are safeguards for supported tools. **They are not a substitute
for OS-level sandboxing** (use of
[`guix-agent-jail`](https://github.com/sjholtz/guix-terminal-jail) or
other container system is advised). Arbitrary custom shell/write tools
need equivalent restrictions. The public-URL downloader has DNS-based
protection and bounded downloads, but DNS-rebinding risk remains;
enforce network egress restrictions where appropriate.

## Testing and development

Run from the **source directory** (not the candidate workspace):

```bash
cd ~/bin/resume-agent # or your install path
uv run --frozen python -m unittest discover -s tests -p 'test_*.py' -v
uv run --frozen python -m compileall -q agent.py subagents.py permissions.py cli.py main.py models.py utils.py
```

Focused tests for the workspace and URL snapshot additions:

```bash
uv run --frozen python -m unittest \
  tests.test_application_workspace \
  tests.test_job_description_snapshot \
  tests.test_permissions \
  tests.test_graph_config \
  tests.test_local_pdf_permissions -v
```

An optional graph-construction integration test (requires the
installed agent dependencies, but does not itself call the model):

```bash
RUN_RESUME_AGENT_INTEGRATION=1 uv run --frozen python \
  -m unittest discover -s tests -p 'test_graph_runtime_optional.py' -v
```

See `tests/README.md` for a manual HITL smoke-test checklist. The
command-line prototype uses an **in-memory** checkpointer; interrupted
conversations do not survive process restarts.

## Project layout

```text
resume-agent/
├── main.py                  # CLI entry point
├── cli.py                   # chat loop, approvals, optional tracing
├── agent.py                 # orchestrator graph factory
├── subagents.py             # specialist registration
├── models.py                # model selection (Ollama by default)
├── config.py                # standalone workspace configuration helpers
├── backend.py               # /system and /workspace virtual backends
├── permissions.py           # role- and path-specific write policy
├── application_workspace.py  # application paths and source-record creation
├── agents/                  # role memory / AGENTS.md
├── prompts/                 # agent system prompts
├── skills/                  # reusable specialist skills
├── tools/                   # research, PDF, application initializer
├── tests/                   # unittest suites and smoke-test guidance
├── pyproject.toml
└── uv.lock
```

## Current limitations and roadmap

This is an evolving prototype, not a verified end-to-end application
publishing service.

- **Next: document reliability.** Enforce selection/use of actual
  LaTeX templates; compile both documents with `latexmk`; inspect PDFs
  and cover-letter length.
- **Next: factual QA.** Trace material résumé/cover-letter claims,
  especially metrics and credentials, to candidate evidence; detect
  unsupported assertions before delivery.
- **Snapshot limitations.** Public HTML extraction cannot handle all
  JavaScript, login-only, anti-bot, or PDF postings. Successful
  extraction still requires content verification.
- **Runtime limitations.** The CLI keeps checkpoints in memory; the
  repository does not provide a production service, durable queue, or
  hosted user interface.
- **Portability.** The current default provider and some path
  assumptions target a local Ollama/Guix workflow. Configure
  equivalents when running elsewhere.

## Privacy, license, and contributions

Candidate profiles, application outputs, and approval records can
contain personal information. Keep them out of public Git
repositories; do not commit credentials, `.env` files, model-provider
secrets, or proprietary job documents. Use candidate-controlled
storage and review every generated artifact before submission.

Contributions and patches should preserve existing candidate-profile
read-only protections, human approval gates, stable evidence/FUP
identifiers, and regression coverage. Run the test suite and document
any known failures alongside changes.

Developed in collaboration with [OpenAI ChatGPT](https://chatgpt.com)
and other tools.
