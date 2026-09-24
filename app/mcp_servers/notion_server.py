"""Serveur MCP « notion-veille » (stdio) : publication de pages de veille dans Notion.

Pourquoi un serveur dédié plutôt que @notionhq/notion-mcp-server ? Le schéma
de blocs exposé par ce dernier pour `children` se limite aux paragraphes et
puces ; la page de veille utilise titres, encadrés, sommaire et séparateurs.
Le serveur officiel reste déclaré pour Claude Code (lecture, recherche).

Variables : NOTION_TOKEN (jeton d'intégration interne, obligatoire),
NOTION_API_URL (défaut https://api.notion.com ; surchargé par les tests E2E).
"""

import os

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("notion-veille", log_level="WARNING")

NOTION_VERSION = "2022-06-28"
MAX_CHILDREN = 100  # limite de l'API par requête


def _client() -> httpx.Client:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise RuntimeError("NOTION_TOKEN absent")
    return httpx.Client(
        base_url=os.environ.get("NOTION_API_URL", "https://api.notion.com"),
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        timeout=30,
    )


def _check(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        raise RuntimeError(f"Notion {response.status_code} : {response.text[:300]}")
    return response.json()


def _append(client: httpx.Client, block_id: str, children: list[dict]) -> int:
    for start in range(0, len(children), MAX_CHILDREN):
        _check(client.patch(
            f"/v1/blocks/{block_id}/children",
            json={"children": children[start : start + MAX_CHILDREN]},
        ))
    return len(children)


@mcp.tool()
def create_page(parent_page_id: str, title: str, children: list[dict], icon: str = "🛰️") -> dict:
    """Crée une sous-page (blocs au format de l'API Notion, découpés par 100)."""
    with _client() as client:
        page = _check(client.post("/v1/pages", json={
            "parent": {"page_id": parent_page_id},
            "icon": {"type": "emoji", "emoji": icon},
            "properties": {"title": {"title": [{"type": "text", "text": {"content": title[:2000]}}]}},
            "children": children[:MAX_CHILDREN],
        }))
        _append(client, page["id"], children[MAX_CHILDREN:])
    return {"id": page["id"], "url": page.get("url", ""), "blocks": len(children)}


@mcp.tool()
def append_blocks(block_id: str, children: list[dict]) -> dict:
    """Ajoute des blocs à une page ou à un bloc existant."""
    with _client() as client:
        return {"appended": _append(client, block_id, children)}


@mcp.tool()
def archive_page(page_id: str) -> dict:
    """Archive une page (réversible depuis la corbeille Notion)."""
    with _client() as client:
        page = _check(client.patch(f"/v1/pages/{page_id}", json={"archived": True}))
    return {"id": page["id"], "archived": page.get("archived", True)}


@mcp.tool()
def search_pages(query: str, limit: int = 10) -> dict:
    """Recherche de pages partagées avec l'intégration."""
    with _client() as client:
        payload = _check(client.post("/v1/search", json={
            "query": query,
            "filter": {"property": "object", "value": "page"},
            "page_size": max(1, min(limit, 50)),
        }))
    pages = []
    for page in payload.get("results", []):
        title = next(
            ("".join(t.get("plain_text", "") for t in prop.get("title", []))
             for prop in page.get("properties", {}).values() if prop.get("type") == "title"),
            "",
        )
        pages.append({"id": page["id"], "url": page.get("url", ""), "title": title})
    return {"pages": pages}


if __name__ == "__main__":
    mcp.run()
