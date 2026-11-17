# ~/bin/resume-agent/tools/search.py
"""SearXNG web search tool for the career-agent system."""

from __future__ import annotations

import os
from typing import Literal
from urllib.parse import urljoin

import requests
from langchain_core.tools import tool

BASE_URL = os.getenv(
    "SEARXNG_BASE_URL",
    "http://localhost/searxng/",
)

SEARCH_URL = urljoin(BASE_URL.rstrip("/") + "/", "search")

SearchCategory = Literal["general", "news", "it", "science"]
TimeRange = Literal["all", "day", "month", "year"]


@tool
def web_search(
    query: str,
    max_results: int = 8,
    category: SearchCategory = "general",
    time_range: TimeRange = "all",
) -> dict:
    """Search the web for career, company, job, and technical research.

    Use for employer information, job requirements, technology
    documentation, hiring trends, and recent announcements.

    Choose 'general' for broad research, 'news' for announcements,
    'it' for technical subjects, or 'science' for research.

    Returns source URLs and snippets. Search results are not
    verified evidence of the candidate's qualifications.
    """
    query = query.strip()

    if not query:
        return {
            "ok": False,
            "error": "Search query must not be empty.",
            "results": [],
        }

    if not 1 <= max_results <= 20:
        return {
            "ok": False,
            "error": "max_results must be between 1 and 20.",
            "results": [],
        }

    params = {
        "q": query,
        "format": "json",
        "categories": category,
    }

    if time_range != "all":
        params["time_range"] = time_range

    try:
        response = requests.get(
            SEARCH_URL,
            params=params,
            timeout=(3, 20),
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()

        data = response.json()
        raw_results = data.get("results", [])

        if not isinstance(raw_results, list):
            raise ValueError("Invalid SearXNG results format")

        results = []
        seen_urls = set()

        for item in raw_results:
            if not isinstance(item, dict):
                continue

            url = item.get("url")
            if not isinstance(url, str) or not url:
                continue

            if url in seen_urls:
                continue

            seen_urls.add(url)

            results.append(
                {
                    "title": item.get("title", ""),
                    "url": url,
                    "snippet": item.get("content", ""),
                    "engine": item.get("engine"),
                    "published_date": item.get("publishedDate"),
                }
            )

            if len(results) >= max_results:
                break

        return {
            "ok": True,
            "query": query,
            "category": category,
            "time_range": time_range,
            "count": len(results),
            "results": results,
        }

    except requests.exceptions.RequestException as exc:
        return {
            "ok": False,
            "query": query,
            "error": f"SearXNG request failed: {exc}",
            "results": [],
        }

    except (ValueError, TypeError) as exc:
        return {
            "ok": False,
            "query": query,
            "error": f"Invalid SearXNG response: {exc}",
            "results": [],
        }
