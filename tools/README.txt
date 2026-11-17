Career-Agent read_web_pdf tool

Install dependencies:
    uv add requests pymupdf langchain-core

Copy read_web_pdf.py into your project's tools/ directory.

This is a read-only public PDF tool. Register read_web_pdf as a
LangChain tool and include it in your human approval configuration.

Test the installation:
    uv run python -m unittest discover -s tests -v
