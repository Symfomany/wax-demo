"""E2E MCP : vrai serveur github-scout (stdio) ↔ langchain-mcp-adapters ↔ fausse API GitHub."""

import asyncio
from datetime import date

from langchain_mcp_adapters.client import MultiServerMCPClient

from app.collectors.github_mcp import build_queries, collect_github_mcp, stdio_connection


def test_server_exposes_read_only_tools(fake_github):
    api_url, _ = fake_github
    client = MultiServerMCPClient({"github": stdio_connection(api_url=api_url)})

    tools = asyncio.run(client.get_tools())

    assert {tool.name for tool in tools} == {"search_repositories", "list_releases", "get_readme"}


def test_collector_discovers_repositories_through_mcp(fake_github):
    api_url, requests = fake_github
    audit: list[str] = []

    documents = collect_github_mcp(
        {"queries": ["llm inference"], "per_query": 50, "max_repos": 5, "min_stars": 100},
        api_url=api_url,
        audit_log=audit,
    )

    assert [str(d.url) for d in documents] == [
        "https://github.com/acme/fast-infer",
        "https://github.com/acme/agent-kit",
    ]
    fast = documents[0]
    assert fast.title == "acme/fast-infer : nouveau dépôt open source (420★)"
    assert fast.source == "github" and fast.tags[:2] == ["github-mcp", "Python"]
    # README nettoyé : badges retirés, liens aplatis, injection neutralisée
    assert "img.shields.io" not in fast.summary
    assert "Serves vLLM models" in fast.summary
    assert "[consigne neutralisée]" in fast.summary
    # Guard MCP : perPage=50 demandé, borné à 10 avant d'atteindre l'API
    assert "perPage': 10" in audit[0]
    assert "per_page=10" in requests[0]
    # README absent (404) toléré
    assert documents[1].summary == "Minimal MCP agent toolkit."


def test_excluded_repositories_are_filtered_before_readme_calls(fake_github):
    api_url, requests = fake_github

    documents = collect_github_mcp(
        {"queries": ["llm"], "exclude_keywords": ["toolkit"]}, api_url=api_url
    )

    assert [str(d.url) for d in documents] == ["https://github.com/acme/fast-infer"]
    assert not any("agent-kit" in request for request in requests)


def test_queries_are_bounded_by_creation_date():
    assert build_queries(["llm"], date(2026, 9, 3), 100) == ["llm created:>2026-09-03 stars:>=100"]
