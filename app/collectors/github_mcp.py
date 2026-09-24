"""Découverte de dépôts open source via le serveur MCP github-scout.

Client : langchain-mcp-adapters (outils MCP → outils LangChain), une seule
session stdio par collecte, intercepteur de garde `make_mcp_guard`.
"""

import re
from datetime import date, datetime, timedelta, timezone

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from pydantic import ValidationError

from app.collectors.rss import clean_text
from app.harness.guards import make_mcp_guard, sanitize_untrusted
from app.mcp_client import python_stdio, run_mcp, tool_payload
from app.schemas import Document


def stdio_connection(token: str | None = None, api_url: str | None = None) -> dict:
    return python_stdio("github_server.py", {"GITHUB_TOKEN": token, "GITHUB_API_URL": api_url})


def readme_excerpt(markdown: str, limit: int) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", markdown)  # images / badges
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # liens → texte
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    return sanitize_untrusted(clean_text(text), limit)


def is_excluded(repo: dict, keywords: list[str]) -> bool:
    """Leçon devenue règle : cours, tutoriels, listes « awesome »… ne sont pas des signaux."""
    haystack = " ".join(
        [repo["full_name"], repo.get("description") or "", *repo.get("topics", [])]
    ).lower()
    return any(keyword.lower() in haystack for keyword in keywords)


def build_queries(queries: list[str], since: date, min_stars: int) -> list[str]:
    return [f"{query} created:>{since.isoformat()} stars:>={min_stars}" for query in queries]


async def discover(
    queries: list[str],
    connection: dict,
    max_repos: int = 8,
    per_query: int = 5,
    readme_chars: int = 1200,
    audit_log: list[str] | None = None,
    exclude_keywords: list[str] | None = None,
) -> list[Document]:
    client = MultiServerMCPClient({"github": connection})
    guard = make_mcp_guard(audit_log)

    async with client.session("github") as session:
        tools = {
            tool.name: tool
            for tool in await load_mcp_tools(
                session, tool_interceptors=[guard], server_name="github"
            )
        }
        repositories: dict[str, dict] = {}
        for query in queries:
            found = tool_payload(
                await tools["search_repositories"].ainvoke({"query": query, "perPage": per_query})
            )
            for item in found.get("items", []):
                if not is_excluded(item, exclude_keywords or []):
                    repositories.setdefault(item["html_url"], item)

        ranked = sorted(repositories.values(), key=lambda r: r["stargazers_count"], reverse=True)
        documents = []
        for repo in ranked[:max_repos]:
            owner, name = repo["full_name"].split("/", 1)
            readme = tool_payload(
                await tools["get_readme"].ainvoke(
                    {"owner": owner, "repo": name, "maxChars": readme_chars * 3}
                )
            ).get("content", "")
            summary = " — ".join(
                part
                for part in [
                    sanitize_untrusted(repo["description"]),
                    readme_excerpt(readme, readme_chars),
                ]
                if part
            )
            try:
                documents.append(
                    Document(
                        source="github",
                        title=(
                            f"{repo['full_name']} : nouveau dépôt open source "
                            f"({repo['stargazers_count']}★)"
                        ),
                        url=repo["html_url"],
                        published_at=repo.get("created_at"),
                        summary=summary,
                        content=summary,
                        tags=[
                            "github-mcp",
                            *([repo["language"]] if repo.get("language") else []),
                            *repo.get("topics", [])[:3],
                        ],
                    )
                )
            except ValidationError:
                continue
        return documents


def collect_github_mcp(
    config: dict,
    token: str | None = None,
    api_url: str | None = None,
    today: date | None = None,
    audit_log: list[str] | None = None,
) -> list[Document]:
    today = today or datetime.now(timezone.utc).date()
    since = today - timedelta(days=config.get("lookback_days", 21))
    queries = build_queries(config.get("queries", []), since, config.get("min_stars", 100))
    return run_mcp(
        discover(
            queries,
            stdio_connection(token, api_url),
            max_repos=config.get("max_repos", 8),
            per_query=config.get("per_query", 5),
            readme_chars=config.get("readme_chars", 1200),
            audit_log=audit_log,
            exclude_keywords=config.get("exclude_keywords", []),
        )
    )
