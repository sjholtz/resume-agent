"""LangChain model initialization; credentials originate in Guix's proxy.

No .env is loaded. In particular, candidate workspace .env files cannot
replace provider credentials or globally enable LangSmith tracing.
"""

import os  # noqa: F401  # used in commented-out model examples below
from pathlib import Path  # noqa: F401

# from dotenv import dotenv_values
# # Read only application-specific, non-secret LangSmith settings.
# tracing_settings = dotenv_values(Path(__file__).resolve().parent / ".env")
# tracing_enabled = tracing_settings.get("LANGSMITH_TRACING")
# if tracing_enabled is not None:
#     value = tracing_enabled.strip().lower()
#     if value not in {"true", "false"}:
#         raise ValueError("LANGSMITH_TRACING must be true or false")
#     os.environ["LANGSMITH_TRACING"] = value
# if tracing_settings.get("LANGSMITH_PROJECT"):
#     os.environ["LANGSMITH_PROJECT"] = tracing_settings["LANGSMITH_PROJECT"]
from langchain.chat_models import init_chat_model

# from langchain_openai import ChatOpenAI

# model = ChatOpenAI(
#     model="gpt-6-luna",
#     # reasoning_effort="none",
#     # use_responses_api=False,
# )

# strong_model = ChatOpenAI(
#     model="gpt-6.1-sol",
#     # reasoning_effort="none",
#     # use_responses_api=False,
# )

# ═══ Open-Source / Alternative Hosted Models ══════════════════════════════════

# Ollama: run models locally (no API key required)
# langchain-ollama is already installed (default dep)
# Install the Ollama app first: https://ollama.com
# Pull a model first, e.g.:  ollama pull qwen2.5:7b
#
# model = init_chat_model("ollama:gemma4:e4b")

# model = init_chat_model("ollama:gemma4:26b")
# strong_model = model

model = init_chat_model(
    "ollama:qwen3.5:9b",
    num_ctx=32768,
    temperature=0,
    reasoning=False,
)
strong_model = model

# This works to access cloud models on Ollama:
# from langchain_ollama import ChatOllama

# model = ChatOllama(
#     model="nemotron-3-ultra:cloud",
#     base_url="https://ollama.com",
# )

# OpenRouter: hosted open-source models via OpenAI-compatible API
# No extra install needed (langchain-openai is already a default dep)
# Free models available; sign up at openrouter.ai and get an API key
# Requires OPENROUTER_API_KEY in .env
#
# from langchain_openai import ChatOpenAI
# model = ChatOpenAI(model="nvidia/nemotron-3-ultra-550b-a55b:free", base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"])


# ═══ Cloud Provider Models (extra install required, see table above) ═════════
# ─── Azure OpenAI ─────────────────────────────────────────────────────────────
# Install first:  uv sync --extra azure
# Requires AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, OPENAI_API_VERSION in .env
# Set your deployment name directly below (it isn't read from .env)
#
# from langchain_openai import AzureChatOpenAI
# model = AzureChatOpenAI(azure_deployment="gpt-4.1", api_version="2024-12-01-preview")


# ─── AWS Bedrock ──────────────────────────────────────────────────────────────
# Install first:  uv sync --extra bedrock
# Requires AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY in .env
# Set your region directly below (it isn't read from .env)
#
# from langchain_aws import ChatBedrockConverse
# model = ChatBedrockConverse(model_id="anthropic.claude-sonnet-4-6", region_name="us-east-1")


# ─── Google Gemini ────────────────────────────────────────────────────────────
# Install first:  uv sync --extra google
# Requires GOOGLE_API_KEY in .env
#
# model = init_chat_model("google_genai:gemini-2.5-flash")
