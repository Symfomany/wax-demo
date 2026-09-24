"""Serveur MCP « github-scout » (stdio) : découverte de dépôts open source.

Les noms d'outils reprennent ceux du serveur officiel github/github-mcp-server
(search_repositories, list_releases) pour pouvoir basculer de l'un à l'autre.
Autonome (httpx + mcp uniquement) : lancé par l'application via
langchain-mcp-adapters et par Claude Code via .mcp.json.

Variables : GITHUB_TOKEN (facultatif, 60 → 5000 req/h),
GITHUB_API_URL (défaut https://api.github.com ; surchargé par les tests E2E).
"""

import base64
import os

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("github-scout", log_level="WARNING")

MAX_PER_PAGE = 20


def _client() -> httpx.Client:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "llm-watch-harness/0.3",
    }
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(
        base_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        headers=headers,
        timeout=20,
    )


def _repository(item: dict) -> dict:
    return {
        "full_name": item["full_name"],
        "html_url": item["html_url"],
        "description": item.get("description") or "",
        "stargazers_count": item.get("stargazers_count", 0),
        "language": item.get("language"),
        "topics": item.get("topics", []),
        "license": (item.get("license") or {}).get("spdx_id"),
        "created_at": item.get("created_at"),
        "pushed_at": item.get("pushed_at"),
    }


@mcp.tool()
def search_repositories(query: str, perPage: int = 10, sort: str = "stars") -> dict:
    """Recherche des dépôts GitHub (syntaxe de recherche GitHub, ex. 'llm created:>2026-09-01')."""
    with _client() as client:
        response = client.get(
            "/search/repositories",
            params={
                "q": query,
                "sort": sort,
                "order": "desc",
                "per_page": max(1, min(perPage, MAX_PER_PAGE)),
            },
        )
        response.raise_for_status()
        payload = response.json()
    return {
        "total_count": payload.get("total_count", 0),
        "items": [_repository(item) for item in payload.get("items", [])],
    }


@mcp.tool()
def list_releases(owner: str, repo: str, perPage: int = 3) -> dict:
    """Dernières releases publiées d'un dépôt."""
    with _client() as client:
        response = client.get(
            f"/repos/{owner}/{repo}/releases",
            params={"per_page": max(1, min(perPage, MAX_PER_PAGE))},
        )
        response.raise_for_status()
    return {
        "releases": [
            {
                "name": release.get("name") or release["tag_name"],
                "tag_name": release["tag_name"],
                "html_url": release["html_url"],
                "published_at": release.get("published_at"),
                "prerelease": release.get("prerelease", False),
            }
            for release in response.json()
            if not release.get("draft")
        ]
    }


@mcp.tool()
def get_readme(owner: str, repo: str, maxChars: int = 3000) -> dict:
    """Extrait du README d'un dépôt (texte brut tronqué)."""
    with _client() as client:
        response = client.get(f"/repos/{owner}/{repo}/readme")
        if response.status_code == 404:
            return {"content": ""}
        response.raise_for_status()
        raw = base64.b64decode(response.json().get("content", "")).decode("utf-8", "replace")
    return {"content": raw[: max(0, min(maxChars, 8000))]}


if __name__ == "__main__":
    mcp.run()
