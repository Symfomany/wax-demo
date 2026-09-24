"""Page Notion « Veille GenAI — 10 dernières veilles », publiée via le serveur MCP notion-veille.

Mise en page :
- en-tête : couverture (image de la première ressource), encart d'introduction, sommaire ;
- dernière veille, développée : titre, résumé, description chiffrée, mots-clés, puis une
  fiche par ressource (titre lié, image, résumé, « pourquoi c'est important », métadonnées) ;
- veilles précédentes : sections repliables (titre, résumé, description, liens, mots-clés).

Les images sont les `og:image` des pages sources, récupérées sans risque (http(s) public,
voir `fetch_og_images`) ; une ressource sans image garde sa fiche texte. La page est
reconstruite à chaque synchronisation ; l'ancienne est archivée (réversible depuis la corbeille).
"""

from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

import httpx
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from app import storage
from app.harness.guards import make_mcp_guard, notion_policy
from app.keywords import extract_keywords
from app.mcp_client import python_stdio, run_mcp, tool_payload
from app.reports import french_date

MAX_TEXT = 1900  # limite Notion : 2000 caractères par segment de texte
SOURCE_ICONS = {"rss": "📰", "arxiv": "📄", "github": "🐙", "github-mcp": "🆕"}
SOURCE_LABELS = {"rss": "blog", "arxiv": "arXiv", "github": "release GitHub", "github-mcp": "nouveau dépôt"}
ImageLookup = Callable[[list[str]], dict[str, str]]


def _text(content: str, url: str | None = None, bold: bool = False, code: bool = False,
          italic: bool = False, color: str | None = None) -> dict:
    segment = {"type": "text", "text": {"content": content[:MAX_TEXT]}}
    if url:
        segment["text"]["link"] = {"url": url}
    annotations = {key: True for key, on in (("bold", bold), ("code", code), ("italic", italic)) if on}
    if color:
        annotations["color"] = color
    if annotations:
        segment["annotations"] = annotations
    return segment


def _block(kind: str, rich_text: list[dict], **extra) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text, **extra}}


def _callout(rich_text: list[dict], emoji: str, color: str) -> dict:
    return _block("callout", rich_text, icon={"type": "emoji", "emoji": emoji}, color=color)


def _image(url: str) -> dict:
    return {"object": "block", "type": "image", "image": {"type": "external", "external": {"url": url}}}


DIVIDER = {"object": "block", "type": "divider", "divider": {}}


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _kind(item: dict) -> str:
    return "github-mcp" if "github-mcp" in item.get("tags", []) else item["source"]


def _description(digest: dict, generated: datetime) -> list[dict]:
    items = digest["items"]
    sources = Counter(_kind(item) for item in items)
    parts = " · ".join(f"{SOURCE_ICONS.get(k, '•')} {SOURCE_LABELS.get(k, k)} ×{n}" for k, n in sources.most_common())
    return [
        _text("Description : ", bold=True),
        _text(f"veille du {french_date(generated)} · ✅ {len(items)} signal(aux) retenu(s) · "
              f"🗑️ {digest.get('rejected_count', 0)} écarté(s)" + (f" · {parts}" if parts else "")),
    ]


def _keywords(digest: dict) -> list[dict]:
    keywords = extract_keywords(digest)
    segments = [_text("🏷️ Mots-clés : ", bold=True)]
    for index, keyword in enumerate(keywords):
        segments.append(_text(keyword, code=True))
        if index < len(keywords) - 1:
            segments.append(_text(" "))
    return segments if keywords else [_text("🏷️ Mots-clés : —")]


def _tags(item: dict) -> list[str]:
    technical = {"rss", "arxiv", "github", "github-mcp", "prerelease"}
    return [t for t in item.get("tags", []) if t not in technical and " " not in t and "/" not in t][:5]


def item_blocks(item: dict, image: str | None = None) -> list[dict]:
    """Fiche d'une ressource : titre lié, image, résumé, pourquoi c'est important, métadonnées."""
    kind = _kind(item)
    meta = [_text(f"{SOURCE_ICONS.get(kind, '•')} {SOURCE_LABELS.get(kind, kind)}", color="gray")]
    if item.get("date"):
        meta.append(_text(f" · 📅 {item['date'][:10]}", color="gray"))
    meta.append(_text(" · 🔗 ", color="gray"))
    meta.append(_text(urlsplit(item["url"]).netloc.removeprefix("www."), url=item["url"], color="gray"))
    if tags := _tags(item):
        meta.append(_text(" · " + " ".join(f"#{t}" for t in tags), color="gray"))
    blocks = [_block("heading_3", [_text(f"{SOURCE_ICONS.get(kind, '•')} "), _text(item["title"], url=item["url"])])]
    if image:
        blocks.append(_image(image))
    if item.get("summary"):
        blocks.append(_block("paragraph", [_text(item["summary"])]))
    blocks.append(_callout([_text("Pourquoi c'est important : ", bold=True), _text(item["why_it_matters"])],
                           "💡", "yellow_background"))
    blocks.append(_block("paragraph", meta))
    return blocks


def digest_blocks(record: dict, images: dict[str, str] | None = None) -> list[dict]:
    """Dernière veille, développée : titre, résumé, description, mots-clés et fiches ressources."""
    digest = record["digest"]
    generated = _parse(digest["generated_at"])
    images = images or {}
    items = digest["items"]
    top = items[0]["title"] if items else "aucun signal retenu"
    blocks = [
        _block("heading_1", [_text(f"🛰️ Veille du {generated:%d/%m/%Y} — {top}")]),
        _callout([_text("Résumé — ", bold=True), _text(digest["executive_summary"])], "📝", "gray_background"),
        _block("paragraph", _description(digest, generated)),
        _block("paragraph", _keywords(digest)),
        _block("heading_2", [_text(f"📚 Ressources ({len(items)})")]),
    ]
    for item in items:
        blocks.extend(item_blocks(item, images.get(item["url"])))
    if not items:
        blocks.append(_block("paragraph", [_text("Aucune ressource.")]))
    blocks.append(DIVIDER)
    return blocks


def archive_blocks(record: dict) -> list[dict]:
    """Veille précédente : section repliable (titre, résumé, description, liens, mots-clés)."""
    digest = record["digest"]
    generated = _parse(digest["generated_at"])
    items = digest["items"]
    children = [
        _callout([_text(digest["executive_summary"])], "📝", "gray_background"),
        _block("paragraph", _description(digest, generated)),
        *(_block("bulleted_list_item", [
            _text(f"{SOURCE_ICONS.get(_kind(item), '•')} "),
            _text(item["title"], url=item["url"], bold=True),
            _text(f" — {item['why_it_matters']}"),
        ]) for item in items[:40]),
        _block("paragraph", _keywords(digest)),
    ]
    top = items[0]["title"] if items else "aucun signal retenu"
    return [_block("heading_2", [_text(f"🗓️ Veille du {generated:%d/%m/%Y} — {top}")],
                   is_toggleable=True, children=children)]


def review_blocks(record: dict) -> list[dict]:
    """Review d'une actualité (onglet Review) : section repliable — titre lié, synthèse, pourquoi,
    scores, points clés, affirmations étayées (avec citation), risques, métadonnées."""
    analysis, page = record["analysis"], record["page"]
    supported = [c for c in analysis.get("claims", []) if c.get("status") == "etaye"]
    meta = [_text(f"🌐 {page.get('site') or urlsplit(page['final_url']).netloc}", color="gray")]
    if page.get("published_at"):
        meta.append(_text(f" · 📅 {page['published_at'][:10]}", color="gray"))
    meta += [_text(" · 🔗 ", color="gray"), _text("article", url=page["final_url"], color="gray"),
             _text(f" · révision {record.get('revision', 1)} · {record.get('model', '')}", color="gray")]
    children = [
        _callout([_text("Synthèse — ", bold=True), _text(analysis["summary"])], "📝", "gray_background"),
        _callout([_text("Pourquoi c'est important : ", bold=True), _text(analysis["why_it_matters"])],
                 "💡", "yellow_background"),
        _block("paragraph", [_text(
            f"🎯 Pertinence {analysis['relevance']}/10 · 🆕 Nouveauté {analysis['novelty']}/10 · "
            f"🔒 Confiance {analysis['confidence']}/10 · source {analysis.get('source_type', '?')}"
            + (f" · domaines : {', '.join(page.get('domains', []))}" if page.get("domains") else ""), bold=True)]),
        *(_block("bulleted_list_item", [_text(point)]) for point in analysis.get("key_points", [])[:8]),
    ]
    for claim in supported[:6]:
        children.append(_block("bulleted_list_item", [_text("✓ ", color="green"), _text(claim["claim"])]))
        if claim.get("quote"):
            children.append(_block("quote", [_text(f"« {claim['quote']} »", italic=True)]))
    children += [_block("bulleted_list_item", [_text("⚠ ", color="orange"), _text(risk)])
                 for risk in analysis.get("risks", [])[:5]]
    children.append(_block("paragraph", meta))
    return [_block("heading_3", [_text("🔬 "), _text(record["title"], url=page["final_url"])],
                   is_toggleable=True, children=children[:90])]


def reviews_section(reviews: list[dict]) -> list[dict]:
    if not reviews:
        return []
    blocks = [_block("heading_1", [_text(f"🔬 Reviews d'actualités ({len(reviews)})")])]
    for record in reviews:
        blocks.extend(review_blocks(record))
    return blocks + [DIVIDER]


def page_blocks(records: list[dict], now: datetime | None = None, images: dict[str, str] | None = None,
                reviews: list[dict] | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    total = sum(len(r["digest"]["items"]) for r in records)
    intro = _callout(
        [_text("Veille GenAI", bold=True), _text(
            f" — {len(records)} dernière(s) veille(s) LLM / GenAI, {total} ressource(s). Mise à jour le "
            f"{french_date(now)} à {now:%H:%M} UTC. Chaque ressource renvoie à sa source primaire ; "
            "les veilles précédentes sont repliées plus bas."
        )],
        "📡", "blue_background",
    )
    blocks = [intro, {"object": "block", "type": "table_of_contents", "table_of_contents": {}}, DIVIDER]
    if records:
        blocks.extend(digest_blocks(records[0], images))
    blocks.extend(reviews_section(reviews or []))  # reviews publiées : conservées à chaque reconstruction
    if records:
        if len(records) > 1:
            blocks.append(_block("heading_1", [_text("🗂️ Veilles précédentes")]))
        for record in records[1:]:
            blocks.extend(archive_blocks(record))
    return blocks


# --- Images des ressources (og:image) ------------------------------------------------------

GENERIC_IMAGE = ("logo", "favicon", "avatar", "placeholder", "default")


def _og_image(url: str) -> str | None:
    from app.review import FetchError, _PageParser, download

    try:
        final_url, _, html = download(url, httpx.Client(timeout=8, headers={"User-Agent": "Mozilla/5.0 (LLMWatchHarness)"}))
    except (FetchError, httpx.HTTPError, ValueError):
        return None
    parser = _PageParser()
    try:
        parser.feed(html[:400_000])
    except Exception:  # noqa: BLE001 — HTML exotique : pas d'image
        return None
    image = parser.meta.get("og:image") or parser.meta.get("twitter:image") or parser.meta.get("og:image:url")
    if not image:
        return None
    image = urljoin(final_url, image.strip())
    if not image.startswith("https://") or any(word in image.lower() for word in GENERIC_IMAGE) or len(image) > 1500:
        return None
    return image


def fetch_og_images(urls: list[str], workers: int = 6) -> dict[str, str]:
    """Image d'aperçu de chaque page (en parallèle) ; les pages sans image sont omises."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        found = dict(zip(urls, pool.map(_og_image, urls)))
    return {url: image for url, image in found.items() if image}


async def _publish(connection_config: dict, parent_page_id: str, title: str, blocks: list[dict],
                   previous_page: str | None, audit_log: list[str] | None, cover: str | None = None) -> dict:
    owned = {previous_page} if previous_page else set()
    guard = make_mcp_guard(audit_log, policy=notion_policy(parent_page_id, owned))
    client = MultiServerMCPClient({"notion": connection_config})
    async with client.session("notion") as session:
        tools = {
            tool.name: tool
            for tool in await load_mcp_tools(session, tool_interceptors=[guard], server_name="notion")
        }
        page = tool_payload(await tools["create_page"].ainvoke(
            {"parent_page_id": parent_page_id, "title": title, "children": blocks, "icon": "🛰️", "cover_url": cover or ""}
        ))
        archived = None
        if previous_page:
            archived = tool_payload(await tools["archive_page"].ainvoke({"page_id": previous_page}))["id"]
        return {"page_id": page["id"], "url": page["url"], "blocks": page["blocks"], "archived": archived}


def check_access(token: str, parent_page_id: str, api_url: str | None = None) -> tuple[bool, str]:
    """Diagnostic en lecture seule : le jeton est valide et la page parente est partagée avec l'intégration."""
    from app.mcp_servers.notion_server import NOT_SHARED_HINT, NOTION_VERSION

    headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION}
    try:
        with httpx.Client(base_url=api_url or "https://api.notion.com", headers=headers, timeout=15) as client:
            me = client.get("/v1/users/me")
            if me.status_code != 200:
                return False, f"jeton refusé (HTTP {me.status_code}) → vérifier NOTION_TOKEN"
            bot = me.json()
            name = f"intégration « {bot.get('name')} » ({(bot.get('bot') or {}).get('workspace_name', '?')})"
            page = client.get(f"/v1/pages/{parent_page_id}")
    except httpx.HTTPError as error:
        return False, f"API Notion injoignable : {error}"
    if page.status_code == 404:
        return False, f"{name} : {NOT_SHARED_HINT}"
    if page.status_code != 200:
        return False, f"{name} : page parente en erreur (HTTP {page.status_code})"
    if page.json().get("archived") or page.json().get("in_trash"):
        return False, f"{name} : la page parente est dans la corbeille"
    return True, f"{name}, page parente accessible"


def sync_notion(
    connection,
    token: str,
    parent_page_id: str,
    api_url: str | None = None,
    limit: int = 10,
    audit_log: list[str] | None = None,
    now: datetime | None = None,
    image_lookup: ImageLookup | None = None,
) -> dict:
    """Publie la page ; `image_lookup` (ex. fetch_og_images) illustre les ressources de la dernière veille."""
    records = storage.recent_digests(connection, limit=limit)
    if not records:
        raise RuntimeError("Aucune veille publiée : rien à synchroniser dans Notion.")
    now = now or datetime.now(timezone.utc)
    previous = storage.current_notion_page(connection)
    latest = [item["url"] for item in records[0]["digest"]["items"]]
    images = image_lookup(latest) if image_lookup and latest else {}
    result = run_mcp(_publish(
        python_stdio("notion_server.py", {"NOTION_TOKEN": token, "NOTION_API_URL": api_url}),
        parent_page_id,
        f"Veille GenAI — {len(records)} dernières veilles ({now:%d/%m/%Y})",
        page_blocks(records, now, images, storage.published_reviews(connection)),
        previous["page_id"] if previous else None,
        audit_log,
        cover=next((images[url] for url in latest if url in images), None),
    ))
    storage.record_notion_page(connection, result["page_id"], result["url"], [r["id"] for r in records])
    return result | {"digests": len(records)}


# --- Publication d'une review (onglet Review) ----------------------------------------------------


async def _append(connection_config: dict, parent_page_id: str, page_id: str, blocks: list[dict],
                  audit_log: list[str] | None) -> dict:
    guard = make_mcp_guard(audit_log, policy=notion_policy(parent_page_id, {page_id}))
    client = MultiServerMCPClient({"notion": connection_config})
    async with client.session("notion") as session:
        tools = {tool.name: tool
                 for tool in await load_mcp_tools(session, tool_interceptors=[guard], server_name="notion")}
        return tool_payload(await tools["append_blocks"].ainvoke({"block_id": page_id, "children": blocks}))


def publish_review(connection, review_id: str, token: str, parent_page_id: str, api_url: str | None = None,
                   audit_log: list[str] | None = None, now: datetime | None = None) -> dict:
    """Ajoute la review à la page de veille active (seule page que la veille peut compléter), puis la
    marque publiée : elle est reprise dans la section « Reviews » à chaque reconstruction de la page."""
    record = storage.get_review(connection, review_id)
    if record is None:
        raise RuntimeError("Review inconnue.")
    page = storage.current_notion_page(connection)
    if page is None:
        raise RuntimeError("Aucune page Notion de veille : publiez d'abord la page (onglet Rapports, "
                           "« Mets à jour la page Notion » ou `notion-sync`).")
    blocks = review_blocks(record)
    result = run_mcp(_append(
        python_stdio("notion_server.py", {"NOTION_TOKEN": token, "NOTION_API_URL": api_url}),
        parent_page_id, page["page_id"], blocks, audit_log,
    ))
    now = now or datetime.now(timezone.utc)
    stored = {key: value for key, value in record.items() if key not in {"created_at", "updated_at"}}
    storage.save_review(connection, stored | {"notion": {"page_url": page["url"], "published_at": now.isoformat()}})
    return {"url": page["url"], "blocks": result.get("appended", len(blocks)), "title": record["title"]}
