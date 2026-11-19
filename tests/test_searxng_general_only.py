"""Offline tests for the fixed SearXNG category.

Run from the repository root:
    python -m unittest discover -s tests -p 'test_searxng_general_only.py' -v

Tests do not need a live SearXNG instance or an installed LangChain package.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType
import unittest
from unittest.mock import patch


SEARCH_PATH = Path(__file__).resolve().parents[1] / "tools" / "search.py"


def load_search_module():
    """Load source under a test-only name and stub only the @tool decorator."""
    core = ModuleType("langchain_core")
    core_tools = ModuleType("langchain_core.tools")
    core_tools.tool = lambda func: func
    spec = importlib.util.spec_from_file_location("searxng_general_test_module", SEARCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"langchain_core": core, "langchain_core.tools": core_tools}):
        spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, entries=None):
        self.entries = entries if entries is not None else []

    def raise_for_status(self):
        return None

    def json(self):
        return {"results": self.entries}


class SearxngGeneralOnlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.search = load_search_module()

    def test_public_tool_signature_has_no_category_choice(self):
        sig = inspect.signature(self.search.web_search)
        self.assertEqual(list(sig.parameters), ["query", "max_results", "time_range"])
        with self.assertRaises(TypeError):
            self.search.web_search("python agent framework", category="it")

    def test_default_search_uses_only_general(self):
        with patch.object(self.search.requests, "get", return_value=FakeResponse()) as get:
            result = self.search.web_search("career agent")
        self.assertTrue(result["ok"])
        self.assertEqual(result["category"], "general")
        kwargs = get.call_args.kwargs
        self.assertEqual(kwargs["params"], {
            "q": "career agent", "format": "json", "categories": "general"
        })
        self.assertNotIn("time_range", kwargs["params"])

    def test_technical_queries_still_use_general(self):
        with patch.object(self.search.requests, "get", return_value=FakeResponse()) as get:
            result = self.search.web_search("LangChain Deep Agents docs", time_range="month")
        self.assertTrue(result["ok"])
        self.assertEqual(get.call_args.kwargs["params"]["categories"], "general")
        self.assertEqual(get.call_args.kwargs["params"]["time_range"], "month")

    def test_search_preserves_normal_result_format_and_deduplication(self):
        entries = [
            {"title": "First", "url": "https://example.com/a", "content": "One", "engine": "test"},
            {"title": "Duplicate", "url": "https://example.com/a", "content": "Two"},
            {"title": "Second", "url": "https://example.com/b", "content": "Three"},
        ]
        with patch.object(self.search.requests, "get", return_value=FakeResponse(entries)):
            result = self.search.web_search("python", max_results=2)
        self.assertEqual(result["count"], 2)
        self.assertEqual([x["title"] for x in result["results"]], ["First", "Second"])
        self.assertEqual(result["results"][0]["snippet"], "One")

    def test_empty_query_does_not_contact_server(self):
        with patch.object(self.search.requests, "get") as get:
            result = self.search.web_search("   ")
        self.assertFalse(result["ok"])
        get.assert_not_called()

    def test_max_results_validation_does_not_contact_server(self):
        with patch.object(self.search.requests, "get") as get:
            result = self.search.web_search("search", max_results=21)
        self.assertFalse(result["ok"])
        get.assert_not_called()

    def test_http_failure_returns_structured_error(self):
        with patch.object(self.search.requests, "get", side_effect=self.search.requests.exceptions.Timeout("timeout")):
            result = self.search.web_search("search")
        self.assertFalse(result["ok"])
        self.assertIn("request failed", result["error"].lower())


if __name__ == "__main__":
    unittest.main()
