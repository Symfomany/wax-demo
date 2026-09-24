"""Outils de l'assistant de veille (outils LangChain : tracés dans Langfuse / LangSmith).

Chaque outil renvoie un `ToolResult` :
- `sources`  : ressources numérotées [1], [2]… que la réponse peut citer ;
- `context`  : texte fourni au LLM pour rédiger la réponse ;
- `direct`   : réponse déterministe (actions) — le LLM n'est alors pas appelé ;
- `data`     : données pour l'interface (ex. identifiant du run lancé).
"""

from __future__ import annotations

import re

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.tools import StructuredTool
from langgraph.store.base import BaseStore

from app import storage
from app.harness.guards import sanitize_untrusted
from app.knowledge import KnowledgeBase, load_knowledge
from app.memory import WatchMemory
from app.reports import list_reports
from app.review import best_passages, record_as_text


@dataclass
class ToolResult:
    summary: str
    sources: list[dict] = field(default_factory=list)
    context: str = ""
    direct: str | None = None
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ChatServices:
    connection: object
    store: BaseStore
    reports_dir: Path
    start_run: Callable[[dict], str] | None = None
    notion_sync: Callable | None = None
    github_search: Callable[[str], list] | None = None
    knowledge: Callable[[], KnowledgeBase] = load_knowledge


def _numbered(sources: list[dict]) -> list[dict]:
    return [source | {"n": index} for index, source in enumerate(sources, start=1)]


def build_tools(services: ChatServices) -> dict[str, StructuredTool]:
    def search_watch(query: str) -> dict:
        """Recherche plein texte dans tous les documents collectés par la veille."""
        hits = storage.search_documents(services.connection, query, limit=6)
        sources = _numbered([
            {"title": h["title"], "url": h["url"], "source": h["source"],
             "date": (h["published_at"] or "")[:10], "published": h["published"], "summary": h["summary"][:400]}
            for h in hits
        ])
        context = "\n\n".join(
            f"[{s['n']}] {s['title']} ({s['source']}, {s['date'] or 'date non précisée'})"
            f"{' — publié dans un digest' if s['published'] else ''}\n{h['summary']}"
            for s, h in zip(sources, hits)
        )
        return ToolResult(
            summary=f"{len(hits)} document(s) trouvé(s) pour « {query} »",
            sources=sources,
            context=context or "Aucun document ne correspond à cette recherche.",
        ).as_dict()

    def latest_digests(limit: int = 3) -> dict:
        """Dernières veilles publiées : synthèse et signaux retenus."""
        records = storage.recent_digests(services.connection, limit=max(1, min(limit, 5)))
        sources, blocks = [], []
        for record in records:
            digest = record["digest"]
            lines = [f"Veille du {digest['generated_at'][:10]} — synthèse : {digest['executive_summary']}"]
            for item in digest["items"]:
                sources.append({"title": item["title"], "url": item["url"], "source": item["source"],
                                "date": (item.get("date") or "")[:10], "summary": item["summary"][:400],
                                "why": item["why_it_matters"][:300]})
                lines.append(f"[{len(sources)}] {item['title']} — {item['summary']} "
                             f"Pourquoi : {item['why_it_matters']}")
            blocks.append("\n".join(lines))
        return ToolResult(
            summary=f"{len(records)} veille(s) publiée(s) chargée(s)",
            sources=_numbered(sources),
            context="\n\n".join(blocks) or "Aucune veille publiée pour l'instant.",
            direct=None if records else
            "Aucune veille n'a encore été publiée. Dites « lance une veille » pour en produire une.",
        ).as_dict()

    def run_watch(collect: bool = True, keywords: str = "") -> dict:
        """Lance une nouvelle veille (collecte, agents, arrêt avant publication),
        éventuellement ciblée sur des mots-clés séparés par des virgules."""
        if services.start_run is None:
            return ToolResult(summary="lancement indisponible",
                              direct="Le lancement de veille n'est pas disponible ici.").as_dict()
        try:
            focus = [k.strip() for k in re.split(r"[,;]| et ", keywords) if k.strip()]
            run_id = services.start_run({"collect": collect, **({"keywords": focus} if focus else {})})
        except RuntimeError as error:
            return ToolResult(summary="veille déjà en cours", direct=str(error)).as_dict()
        focus_text = f" ciblée sur **{', '.join(focus)}**" if focus else ""
        return ToolResult(
            summary=f"veille lancée ({run_id[:8]})" + (f" · focus : {', '.join(focus)}" if focus else ""),
            direct=(
                f"Veille lancée{focus_text}. Suivez la progression dans le panneau **Veille** : collecte "
                "parallèle, Scout, Critic, Editor, puis validation. Vous pourrez publier ou "
                "rejeter le digest avec une note, qui sera mémorisée."
            ),
            data={"run_id": run_id, "keywords": focus},
        ).as_dict()

    def github_search(query: str) -> dict:
        """Recherche de dépôts GitHub récents via le serveur MCP github-scout."""
        if services.github_search is None:
            return ToolResult(summary="MCP indisponible", direct="La recherche GitHub n'est pas configurée.").as_dict()
        documents = services.github_search(query)
        sources = _numbered([
            {"title": d.title, "url": str(d.url), "source": "github",
             "date": d.published_at.strftime("%Y-%m-%d") if d.published_at else "", "summary": d.summary[:400]}
            for d in documents
        ])
        context = "\n\n".join(f"[{s['n']}] {d.title}\n{d.summary[:500]}" for s, d in zip(sources, documents))
        return ToolResult(
            summary=f"{len(documents)} dépôt(s) via MCP pour « {query} »",
            sources=sources,
            context=context or "Aucun dépôt récent ne correspond.",
        ).as_dict()

    def memory_status() -> dict:
        """Mémoire de veille : leçons humaines, thèmes privilégiés, santé des sources."""
        memory = WatchMemory(services.store)
        health = memory.source_health()
        context = (
            f"Leçons et préférences :\n{memory.prompt_context()}\n\nSanté des sources :\n"
            + ("\n".join(f"- {name} : {v['ok']} OK / {v['failures']} échec(s)"
                         + (f" (dernier : {v['last_error']})" if v["failures"] else "")
                         for name, v in sorted(health.items())) or "- aucune donnée")
        )
        return ToolResult(summary="mémoire chargée", context=context).as_dict()

    def notion_sync() -> dict:
        """Publie la page Notion des 10 dernières veilles (serveur MCP notion-veille)."""
        if services.notion_sync is None:
            return ToolResult(
                summary="Notion non configuré",
                direct="Notion n'est pas configuré : ajoutez `NOTION_TOKEN` et "
                       "`NOTION_PARENT_PAGE_ID` dans `.env` (voir le Getting started).",
            ).as_dict()
        result = services.notion_sync(services.connection)
        return ToolResult(
            summary=f"page Notion publiée ({result['digests']} veilles)",
            sources=_numbered([{"title": "Page Notion de veille", "url": result["url"], "source": "notion", "date": ""}]),
            direct=f"Page Notion mise à jour avec les {result['digests']} dernières veilles [1].",
            data=result,
        ).as_dict()

    def list_reports_tool(limit: int = 5) -> dict:
        """Derniers rapports Markdown datés."""
        reports = list_reports(services.reports_dir)[:limit]
        names = [f"{path.parent.name}/{path.name}" for path in reports]
        return ToolResult(
            summary=f"{len(reports)} rapport(s)",
            direct=("Derniers rapports (onglet **Rapports** pour les lire) :\n"
                    + "\n".join(f"- `{name}`" for name in names)) if reports
            else "Aucun rapport pour l'instant : il est créé à chaque publication de veille.",
            data={"reports": names},
        ).as_dict()

    def grill_me() -> dict:
        """Lance l'entretien Grill-me (questions sur les centres d'intérêt)."""
        return ToolResult(
            summary="entretien Grill-me",
            direct="Je vais te poser des questions, une à la fois, avec ma recommandation pour chacune : "
                   "domaines (LLM, robotique, événements, architecture, nouveaux modèles…), puis chaque branche "
                   "choisie. L'onglet **🎯 Grill-me** s'ouvre.",
            data={"grill": True},
        ).as_dict()

    def knowledge_entries(entries, first: int = 1) -> tuple[list[dict], str]:
        sources = [{"title": e.title, "url": e.url, "source": f"knowledge · {e.kind}", "date": "",
                    "knowledge_id": e.id, "n": n} for n, e in enumerate(entries, start=first)]
        blocks = []
        for source, entry in zip(sources, entries):
            text = "\n".join(f"- {rule}" for rule in entry.rules) if entry.rules else entry.body
            blocks.append(f"[{source['n']}] {entry.title} ({entry.kind}"
                          f"{', ' + entry.domain if entry.domain else ''})\n{sanitize_untrusted(text, 1500)}")
        return sources, "\n\n".join(blocks)

    def knowledge_search(query: str) -> dict:
        """Base de connaissances : glossaire, règles métiers par domaine, notes téléversées."""
        entries = services.knowledge().search(query, limit=4, kinds={"glossaire", "regles", "note"})
        sources, context = knowledge_entries(entries)
        return ToolResult(
            summary=f"{len(entries)} entrée(s) de la base de connaissances pour « {query[:60]} »",
            sources=sources,
            context=context or "Aucune entrée de la base de connaissances ne correspond.",
        ).as_dict()

    def challenge_review(review_id: str, query: str) -> dict:
        """Challenge d'une review : extraits de l'article, synthèse, règles et connaissances."""
        record = storage.get_review(services.connection, review_id)
        if record is None:
            return ToolResult(summary="review introuvable", direct="Cette review n'existe plus.").as_dict()
        page = record["page"]
        article = {"title": record["title"], "url": page["final_url"], "source": page["site"] or "article",
                   "date": page.get("published_at") or ""}
        entries = services.knowledge().search(query, limit=2, kinds={"glossaire", "regles", "note"})
        extra, knowledge_context = knowledge_entries(entries, first=2)
        sources = [article | {"n": 1}, *extra]
        passages = "\n\n".join(sanitize_untrusted(p) for p in best_passages(page["text"], query))
        context = (
            "MODE CHALLENGE : l'utilisateur conteste ou interroge la review ci-dessous. Vérifie chaque point "
            "contre les EXTRAITS de l'article [1]. Si un extrait contredit ou n'étaye pas la review, reconnais-le "
            "et propose la correction ; sinon, défends la review en citant le passage [1]. Ne te fie pas à la "
            "review elle-même comme preuve.\n\n"
            f"ARTICLE [1] : {record['title']} ({page['site']}, {page.get('published_at') or 'date non précisée'})\n\n"
            f"REVIEW :\n{record_as_text(record)}\n\n"
            f"EXTRAITS DE L'ARTICLE [1] :\n{passages}"
            + (f"\n\nBASE DE CONNAISSANCES :\n{knowledge_context}" if knowledge_context else "")
        )
        return ToolResult(
            summary=f"review « {record['title'][:50]} » · {len(entries)} connaissance(s)",
            sources=sources,
            context=context,
            data={"review_id": review_id},
        ).as_dict()

    functions = {
        "search_watch": search_watch,
        "latest_digests": latest_digests,
        "run_watch": run_watch,
        "github_search": github_search,
        "memory_status": memory_status,
        "notion_sync": notion_sync,
        "list_reports": list_reports_tool,
        "grill_me": grill_me,
        "knowledge_search": knowledge_search,
        "challenge_review": challenge_review,
    }
    return {name: StructuredTool.from_function(function, name=name) for name, function in functions.items()}
