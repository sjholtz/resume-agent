# Shared web fetch utilities for career-agent Deep Agents

This package refactors your earlier `read_web_page.py` and `read_web_pdf.py` into two LangChain tools that reuse one HTTP module. The HTML reader retains the corrected Trafilatura config-based `MAX_TREE_SIZE` setting.

## Directory structure

```text
tools/
├── __init__.py
├── web_fetch.py         # common URL validator + bounded downloaders
├── read_web_page.py     # HTML extraction (Trafilatura)
└── read_web_pdf.py      # PDF extraction (PyMuPDF)
tests/
└── test_web_fetch.py    # offline HTTP/SSRF/redirect/limit tests
```

Place the `tools/` files in your project's existing `tools/` Python package. Keep your existing `tools/search.py` unchanged. The `__init__.py` here is empty of imports so it will not override tool registrations, but **do not replace** an existing `__init__.py` if yours has useful content.

## Installation

```bash
pip install requests trafilatura pymupdf langchain-core
```

## What is shared?

`tools.web_fetch` provides:

- `validate_public_url(url)` — best-effort DNS and URL checks, rejecting local/private/non-global addresses and nonstandard ports
- `download_html(url)` — bounded 2 MB HTML download with explicit redirect handling, HTTP timeout, and content-type validation
- `download_pdf(url)` — bounded 15 MB PDF download with the same checks plus a PDF header/signature check
- `DownloadedResource` — a dataclass exposing `.body`, `.final_url`, and `.content_type`

The per-tool extraction and output schemas are unchanged by the HTTP refactor:

```python
# tools/read_web_page.py
from .web_fetch import download_html

response = download_html(url)
raw_html = response.body
final_url = response.final_url
content_type = response.content_type
# then use Trafilatura and apply start_char/max_chars
```

```python
# tools/read_web_pdf.py
from .web_fetch import download_pdf

response = download_pdf(url)
pdf_bytes = response.body
final_url = response.final_url
# then use PyMuPDF and apply page/character pagination
```

These helpers are ordinary Python functions, **not LangChain tools**; only the two decorated readers should be registered as tools.

## Deep Agents registration

```python
from deepagents import create_deep_agent
from langgraph.checkpoint.memory import InMemorySaver
from tools.search import web_search
from tools.read_web_page import read_web_page
from tools.read_web_pdf import read_web_pdf

agent = create_deep_agent(
    tools=[web_search, read_web_page, read_web_pdf],
    interrupt_on={
        "web_search": True,
        "read_web_page": True,
        "read_web_pdf": True,
    },
    checkpointer=InMemorySaver(),
    # model=..., backend=..., skills=..., subagents=...
)
```

The above is illustrative; preserve your existing model, checkpointer and subagent setup. If using separate subagents, configure tool availability and approval policy for each one. Use a persistent checkpointer in production when approvals may be resumed later.

## Test the tools

```python
from tools.read_web_page import read_web_page
from tools.read_web_pdf import read_web_pdf

page = read_web_page.invoke({"url": "https://example.com", "max_chars": 3000})
print(page)

pdf = read_web_pdf.invoke(
    {
        "url": "https://your-public-host.example/path/document.pdf",
        "start_page": 1,
        "max_pages": 3,
        "max_chars": 12000,
    }
)
print(pdf)
```

Replace the illustrative PDF URL with a real public PDF. Follow the PDF tool's `next_start_page` and `next_start_char` when `has_more` is true. HTML uses `start_char` for continuation.

Run offline shared-helper tests from the **project directory**:

```bash
python -m unittest discover -s tests -v
```

## Security limitations

**Not a complete SSRF defense:** `socket.getaddrinfo()` is checked before Requests establishes a connection. An attacker-controlled DNS response can change between validation and connection (DNS rebinding / TOCTOU). Use an egress firewall, container/network policy, or an explicitly validated proxy to block connections to loopback, private, link-local, and cloud metadata endpoints. The tool refuses HTTP(S) ports other than 80 and 443, follows at most three redirects, disables environment-based proxies, and streams responses with byte caps.

**Do not use these helpers to access SearXNG at `http://localhost/searxng/`**. That is a trusted *internal* service and has a different network policy. Keep your existing SearXNG client code separate. Retrieved third-party text is untrusted data, not instructions or evidence that the candidate possesses a qualification.

**PDF parser safety:** For untrusted PDFs, consider running PyMuPDF extraction in a separate process/container with CPU, memory, and time limits. Parsing is not fully protected by the HTTP download byte cap.

**JavaScript/scanned docs:** No browser rendering and no OCR are included. A JavaScript-only HTML page or image-only PDF may have little/no selectable text.
