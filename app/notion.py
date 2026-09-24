"""Page Notion « Veille GenAI — 10 dernières veilles », publiée via le serveur MCP notion-veille.

Pour chaque veille : titre, résumé, description, ressources (liens) et mots-clés.
La page est reconstruite à chaque synchronisation ; l'ancienne est archivée
(réversible depuis la corbeille Notion) pour qu'une seule page reste à jour.
"""

from collections import Counter
from datetime import datetime, timezone

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from app import storage
from app.harness.guards import make_mcp_guard, notion_policy
from app.keywords import extract_keywords
from app.mcp_client import python_stdio, run_mcp, tool_payload
from app.reports import french_date

MAX_TEXT = 1900  # limite Notion : 2000 caractères par segment de texte


def _text(content: str, url: str | None = None, bold: bool = False, code: bool = False) -> dict:
    segment = {"type": "text", "text": {"content": content[:MAX_TEXT]}}
    if url:
        segment["text"]["link"] = {"url": url}
    if bold or code:
        segment["annotations"] = {"bold": bold, "code": code}
    return segment


def _block(kind: str, rich_text: list[dict], **extra) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text, **extra}}


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def digest_blocks(record: dict) -> list[dict]:
    digest = record["digest"]
    generated = _parse(digest["generated_at"])
    items = digest["items"]
    sources = Counter(
        "nouveaux dépôts" if "github-mcp" in item.get("tags", []) else item["source"] for item in items
    )
    top = items[0]["title"] if items else "aucun signal retenu"
    keywords = extract_keywords(digest)

    blocks = [
        # Titre
        _block("heading_2", [_text(f"🛰️ Veille du {generated:%d/%m/%Y} — {top}")]),
        # Résumé
        _block("callout", [_text(digest["executive_summary"])], icon={"type": "emoji", "emoji": "📝"}, color="gray_background"),
        # Description
        _block("paragraph", [
            _text("Description : ", bold=True),
            _text(
                f"veille du {french_date(generated)} · {len(items)} signal(aux) retenu(s), "
                f"{digest.get('rejected_count', 0)} écarté(s) · sources : "
                + (", ".join(f"{name} ×{count}" for name, count in sources.most_common()) or "—")
            ),
        ]),
        # Ressources
        _block("heading_3", [_text("Ressources")]),
    ]
    for item in items:
        blocks.append(_block("bulleted_list_item", [
            _text(item["title"], url=item["url"], bold=True),
            _text(f" — {item['why_it_matters']}"),
        ]))
    if not items:
        blocks.append(_block("paragraph", [_text("Aucune ressource.")]))
    # Mots-clés
    keyword_text = [_text("Mots-clés : ", bold=True)]
    for index, keyword in enumerate(keywords):
        keyword_text.append(_text(keyword, code=True))
        if index < len(keywords) - 1:
            keyword_text.append(_text(" · "))
    blocks.append(_block("paragraph", keyword_text if keywords else [_text("Mots-clés : —")]))
    blocks.append({"object": "block", "type": "divider", "divider": {}})
    return blocks


def page_blocks(records: list[dict], now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    intro = _block(
        "callout",
        [_text(
            f"{len(records)} dernière(s) veille(s) LLM / GenAI, mise à jour le {french_date(now)} "
            f"à {now:%H:%M} UTC. Chaque ressource renvoie à sa source primaire."
        )],
        icon={"type": "emoji", "emoji": "📡"},
        color="blue_background",
    )
    blocks = [intro, {"object": "block", "type": "table_of_contents", "table_of_contents": {}}]
    for record in records:
        blocks.extend(digest_blocks(record))
    return blocks


async def _publish(connection_config: dict, parent_page_id: str, title: str, blocks: list[dict],
                   previous_page: str | None, audit_log: list[str] | None) -> dict:
    owned = {previous_page} if previous_page else set()
    guard = make_mcp_guard(audit_log, policy=notion_policy(parent_page_id, owned))
    client = MultiServerMCPClient({"notion": connection_config})
    async with client.session("notion") as session:
        tools = {
            tool.name: tool
            for tool in await load_mcp_tools(session, tool_interceptors=[guard], server_name="notion")
        }
        page = tool_payload(await tools["create_page"].ainvoke(
            {"parent_page_id": parent_page_id, "title": title, "children": blocks, "icon": "🛰️"}
        ))
        archived = None
        if previous_page:
            archived = tool_payload(await tools["archive_page"].ainvoke({"page_id": previous_page}))["id"]
        return {"page_id": page["id"], "url": page["url"], "blocks": page["blocks"], "archived": archived}


def sync_notion(
    connection,
    token: str,
    parent_page_id: str,
    api_url: str | None = None,
    limit: int = 10,
    audit_log: list[str] | None = None,
    now: datetime | None = None,
) -> dict:
    records = storage.recent_digests(connection, limit=limit)
    if not records:
        raise RuntimeError("Aucune veille publiée : rien à synchroniser dans Notion.")
    now = now or datetime.now(timezone.utc)
    previous = storage.current_notion_page(connection)
    result = run_mcp(_publish(
        python_stdio("notion_server.py", {"NOTION_TOKEN": token, "NOTION_API_URL": api_url}),
        parent_page_id,
        f"Veille GenAI — {len(records)} dernières veilles ({now:%d/%m/%Y})",
        page_blocks(records, now),
        previous["page_id"] if previous else None,
        audit_log,
    ))
    storage.record_notion_page(connection, result["page_id"], result["url"], [r["id"] for r in records])
    return result | {"digests": len(records)}
