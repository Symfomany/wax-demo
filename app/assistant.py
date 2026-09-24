"""Assistant rapide (panneau flottant de l'interface web) : conversation directe avec l'API Claude
(clé CLAUDE_API), indépendante du LLM local et du graphe de chat.

- réponses en streaming (SSE) ;
- option 🌐 : outil serveur `web_search`, les sources citées par Claude sont renvoyées telles quelles ;
- contexte : dernières actus et dernière veille (titres + URL), pour répondre « sur ma veille ».
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import settings
from app.news import claude_client


SYSTEM = """Tu es l'assistant rapide d'une veille LLM/GenAI, intégré à son interface web. Nous sommes le {today}.
Réponds en français, de façon concise et structurée (Markdown léger : listes, **gras**).
Ne jamais inventer une date, un chiffre, une version ou une URL : si tu ne sais pas, dis-le.
Quand la recherche web est disponible, cite les pages consultées.

Contexte de la veille (titres récents, pour information) :
{context}"""


class AssistantMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


def build_context(news: list[dict], digests: list[dict], limit: int = 12) -> str:
    lines = [f"- [{n.get('published_at') or '?'}] {n['title']} ({n['source']}) — {n['url']}" for n in news[:limit]]
    for record in digests[:1]:
        digest = record["digest"]
        lines.append(f"Dernière veille ({digest['generated_at'][:10]}) : {digest['executive_summary'][:600]}")
    return "\n".join(lines) or "Aucun."


def stream_assistant(messages: list[AssistantMessage], context: str = "Aucun.", web: bool = False,
                     client: Any = None, model: str | None = None) -> Iterator[dict]:
    """Événements : {"type": "token", "text"} … puis {"type": "final", "text", "sources", "model", "searches"}."""
    client = client or claude_client()
    model = model or settings.assistant_model
    system = SYSTEM.format(today=datetime.now(timezone.utc).date().isoformat(), context=context)
    history: list[dict] = [m.model_dump() for m in messages[-16:]]
    tools = [{"type": settings.news_search_tool, "name": "web_search", "max_uses": 3}] if web else []
    text, sources, searches = "", {}, 0
    for _ in range(3):  # pause_turn : reprise d'une longue boucle d'outils serveur
        kwargs = {"model": model, "max_tokens": 2048, "system": system, "messages": history}
        if tools:
            kwargs["tools"] = tools
        with client.messages.stream(**kwargs) as stream:
            for event in stream:
                if event.type == "content_block_delta" and getattr(event.delta, "type", "") == "text_delta":
                    text += event.delta.text
                    yield {"type": "token", "text": event.delta.text}
                elif event.type == "content_block_start" and getattr(event.content_block, "type", "") == "server_tool_use":
                    searches += 1
                    query = (getattr(event.content_block, "input", None) or {}).get("query", "")
                    yield {"type": "tool", "text": f"🌐 recherche web{f' : {query}' if query else ''}"}
            final = stream.get_final_message()
        for block in final.content:
            for citation in getattr(block, "citations", None) or []:
                url = getattr(citation, "url", None)
                if url and url not in sources:
                    sources[url] = {"url": url, "title": getattr(citation, "title", "") or url}
        if final.stop_reason != "pause_turn":
            break
        history = [*history, {"role": "assistant", "content": final.content}]
    yield {"type": "final", "text": text, "sources": list(sources.values()), "model": model, "searches": searches}
