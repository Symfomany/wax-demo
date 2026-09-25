"""Graphe principal : un supervisor pilote un Task Graph et délègue aux sous-agents.

    START → supervisor ─┬─ Send×N → collector ──┐
                        ├─ prefilter ───────────┤
                        ├─ quality   (sous-graphe : bruit → doublons → sélection) ┤
                        ├─ research  (sous-graphe map-reduce) ─┤
                        ├─ review    (sous-graphe conditionnel) ┤→ supervisor
                        ├─ editorial (sous-graphe + réparation) ┘
                        ├─ approval (interrupt humain) → publish | reject → reflect → END
                        ├─ blocked (guards) → END
                        └─ failed (agent en erreur) → END

Patterns conditionnels du supervisor :
- tâches prêtes du DAG → fan-out parallèle (collecteurs) ou délégation séquentielle ;
- trop peu de signaux acceptés → relance de research avec un seuil assoupli (borné) ;
- échec d'une tâche obligatoire → `failed` ; violations des guards → `blocked`.
"""

from __future__ import annotations

import httpx
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Command, RetryPolicy, Send, interrupt

from app import storage
from app.harness.guards import node_contract
from app.harness.hooks import document_risks, normalize_title
from app.memory import WatchMemory
from app.publishers import Publication, default_publishers, run_publishers
from app.renderers.markdown import render_markdown
from app.schemas import Digest, Document
from app.workflow.state import (
    CollectorUpdate,
    EditorialUpdate,
    HarnessContext,
    PrefilterUpdate,
    QualityUpdate,
    ResearchUpdate,
    ReviewUpdate,
    WatchState,
)
from app.workflow.quality import DEFAULT_EXCLUSIONS
from app.workflow.subagents import build_editorial, build_quality, build_research, build_review
from app.workflow.tasks import TaskGraph

# Erreurs transitoires (Ollama ou réseau) : relancées par LangGraph.
AGENT_RETRY = RetryPolicy(
    max_attempts=2,
    initial_interval=1.0,
    retry_on=(httpx.TransportError, ConnectionError, TimeoutError),
)
AGENT_NODES = ("research", "review", "editorial")


def build_graph(checkpointer, context: HarnessContext, store: BaseStore | None = None):
    quality_graph = build_quality(context)
    research_graph = build_research(context)
    review_graph = build_review(context)
    editorial_graph = build_editorial(context)

    # --- Supervisor -----------------------------------------------------------

    def supervisor(state: WatchState) -> Command:
        plan = TaskGraph.model_validate(state["plan"])
        status = state.get("status", {})

        if failure := plan.blocking_failure(status):
            return Command(goto="failed", update={"trace": [f"échec bloquant : {failure.id}"]})

        # Pattern conditionnel : relance bornée de la recherche.
        if (
            status.get("review") == "done"
            and status.get("editorial") == "pending"
            and len(state.get("accepted", [])) < context.min_accepted
            and state.get("review_round", 0) < context.max_review_rounds
            and len(state.get("signals", [])) < len(state.get("candidates", []))
        ):
            relaxed = max(3, state["min_relevance"] - 2)
            return Command(
                goto="research",
                update={
                    "status": {"research": "running", "review": "pending"},
                    "review_round": state.get("review_round", 0) + 1,
                    "min_relevance": relaxed,
                    "trace": [
                        f"{len(state.get('accepted', []))} signal(s) accepté(s) < "
                        f"{context.min_accepted} : relance research (seuil {relaxed})"
                    ],
                },
            )

        ready = plan.ready(status)
        if not ready:
            if not plan.complete(status):
                raise RuntimeError(f"Task Graph bloqué : {status}")
            if state.get("violations"):
                return Command(goto="blocked", update={"trace": ["guards : publication bloquée"]})
            return Command(goto="approval", update={"trace": ["plan terminé → validation"]})

        collectors = [task for task in ready if task.agent == "collector"]
        if collectors:
            return Command(
                goto=[Send("collector", {"task": task.model_dump()}) for task in collectors],
                update={
                    "status": {task.id: "running" for task in collectors},
                    "trace": [f"fan-out : {', '.join(task.id for task in collectors)}"],
                },
            )

        task = ready[0]
        return Command(
            goto=task.agent,
            update={"status": {task.id: "running"}, "trace": [f"délègue {task.id} → {task.agent}"]},
        )

    # --- Workers ----------------------------------------------------------------

    @node_contract(CollectorUpdate)
    def collector(payload: dict, store: BaseStore) -> dict:
        task = payload["task"]
        source = task["params"]["source"]
        memory = WatchMemory(store)
        # Guard : l'échec d'une source est isolé (les tâches parallèles
        # voisines ne sont pas affectées) et mémorisé.
        try:
            documents = context.collectors[source]()
            inserted = storage.save_documents(context.connection, documents)
        except Exception as error:  # noqa: BLE001 — frontière d'isolation
            memory.record_source(source, ok=False, detail=str(error))
            return {"status": {task["id"]: "failed"}, "errors": [f"{task['id']} : {error}"]}
        memory.record_source(source, ok=True)
        return {"status": {task["id"]: "done"}, "collected": {source: inserted}}

    @node_contract(PrefilterUpdate)
    def prefilter(state: WatchState) -> dict:
        """Mémoire + dédoublonnage exact + fraîcheur + équilibrage des sources.

        Garde un pool plus large que max_documents : le sous-graphe quality écarte ensuite
        bruit et quasi-doublons avant de retenir les max_documents premiers."""
        now = context.now()
        options = state.get("options") or {}
        max_age = options.get("max_age_days") or context.max_age_days
        max_documents = options.get("max_documents") or context.max_documents
        keywords = [k.lower() for k in options.get("keywords", []) if k.strip()]
        matches = all if options.get("match_all") else any
        already_published = storage.published_urls(context.connection)
        documents = storage.recent_documents(context.connection, limit=300)

        seen_titles: set[str] = set()
        by_source: dict[str, list[Document]] = {}
        documents.sort(key=lambda d: d.published_at.timestamp() if d.published_at else 0, reverse=True)
        for document in documents:
            title = normalize_title(document.title)
            if str(document.url) in already_published or title in seen_titles:
                continue
            if any("plus de" in risk for risk in document_risks(document, now, max_age)):
                continue
            # Veille ciblée : mots-clés de focus demandés pour ce run.
            if keywords and not matches(
                keyword in f"{document.title} {document.summary}".lower() for keyword in keywords
            ):
                continue
            # Règle issue d'une leçon humaine : aussi appliquée aux dépôts déjà en mémoire.
            if "github-mcp" in document.tags and any(
                keyword.lower() in f"{document.title} {document.summary}".lower()
                for keyword in context.exclude_keywords
            ):
                continue
            seen_titles.add(title)
            # github-mcp (dépôts) et github (releases) sont équilibrés séparément.
            key = "github-mcp" if "github-mcp" in document.tags else document.source
            by_source.setdefault(key, []).append(document)

        # Tourniquet entre sources : arXiv ne doit pas écraser les releases.
        pool = max_documents * max(1, context.quality_pool_factor)
        candidates: list[Document] = []
        queues = list(by_source.values())
        while queues and len(candidates) < pool:
            for queue in list(queues):
                if not queue:
                    queues.remove(queue)
                    continue
                candidates.append(queue.pop(0))
                if len(candidates) >= pool:
                    break

        return {
            "status": {"prefilter": "done"},
            "candidates": [doc.model_dump(mode="json") for doc in candidates],
        }

    @node_contract(QualityUpdate)
    def quality(state: WatchState, store: BaseStore) -> dict:
        """Bruit et quasi-doublons écartés avant le Scout (moins d'appels LLM, digest plus net)."""
        interests = WatchMemory(store).interests() or {}
        exclusions = list(dict.fromkeys([*DEFAULT_EXCLUSIONS, *interests.get("exclusions", [])]))
        result = quality_graph.invoke(
            {
                "candidates": state["candidates"],
                "exclusions": exclusions,
                "published": [list(item) for item in storage.published_titles(context.connection)],
                "max_documents": (state.get("options") or {}).get("max_documents") or context.max_documents,
            }
        )
        filtered = result.get("filtered", [])
        noise = sum(1 for item in filtered if item["kind"] == "bruit")
        return {
            "status": {"quality": "done"},
            "candidates": result["kept"],
            "quality": result["quality"],
            "filtered": filtered,
            "trace": [f"quality : {noise} bruit(s), {len(filtered) - noise} doublon(s) écarté(s), "
                      f"{len(result['kept'])} candidat(s) retenu(s)"],
        }

    @node_contract(ResearchUpdate)
    def research(state: WatchState, store: BaseStore) -> dict:
        memory = WatchMemory(store)
        interests = memory.interests() or {}
        keywords = [*(state.get("options") or {}).get("keywords", []), *interests.get("keywords", []),
                    *(tag for tag, _ in memory.top_tags())]
        result = research_graph.invoke(
            {
                "candidates": state["candidates"],
                "min_relevance": state["min_relevance"],
                "memory": focus_note(state) + memory.prompt_context(),
                "quality": state.get("quality", {}),
                "keywords": list(dict.fromkeys(k for k in keywords if k)),
            }
        )
        return {
            "status": {"research": "done"},
            "signals": result["signals"],
            "errors": result.get("errors", []),
        }

    @node_contract(ReviewUpdate)
    def review(state: WatchState) -> dict:
        result = review_graph.invoke({"candidates": state["candidates"], "signals": state["signals"]})
        return {
            "status": {"review": "done"},
            "critiques": result["critiques"],
            "accepted": result["accepted"],
        }

    @node_contract(EditorialUpdate)
    def editorial(state: WatchState, store: BaseStore) -> dict:
        result = editorial_graph.invoke(
            {
                "candidates": state["candidates"],
                "accepted": state["accepted"],
                "rejected_count": len(state["signals"]) - len(state["accepted"]),
                "memory": WatchMemory(store).prompt_context(),
            }
        )
        return {
            "status": {"editorial": "done"},
            "digest": result["digest"],
            "violations": result["violations"],
            "repair_round": result["repair_round"],
        }

    def agent_failed(state: WatchState, error: NodeError) -> Command:
        """error_handler LangGraph : l'échec d'un agent devient un état, pas un crash.

        Le handler ne suit pas les arêtes du nœud en échec : il rend
        explicitement la main au supervisor, qui décide de la suite.
        """
        return Command(
            goto="supervisor",
            update={"status": {error.node: "failed"}, "errors": [f"{error.node} : {error.error}"]},
        )

    # --- Fin de run : humain, publication, mémoire -----------------------------------

    def approval(state: WatchState) -> dict:
        if not context.human_approval:
            return {"approval": True, "note": ""}
        answer = interrupt(
            {
                "action": "review_digest",
                "markdown": render_markdown(state["digest"], state.get("critiques", [])),
                "instruction": "Reprendre avec {'approved': bool, 'note': str}.",
            }
        )
        return {"approval": bool(answer.get("approved", False)), "note": str(answer.get("note", ""))}

    def publish(state: WatchState) -> dict:
        """Déclenche les outils de publication : fichiers, rapport daté, Notion."""
        digest = Digest.model_validate(state["digest"])  # validation avant persistance
        publication = Publication(
            run_id=state["run_id"],
            digest=digest.model_dump(mode="json"),
            critiques=state.get("critiques", []),
            connection=context.connection,
            output_dir=context.output_dir,
            reports_dir=context.reports_dir or context.output_dir.parent / "reports",
            templates_dir=context.templates_dir,
            model=context.llm.model,
            collected=state.get("collected", {}),
            trace=state.get("trace", []),
            errors=state.get("errors", []),
        )
        outputs, errors = run_publishers(publication, default_publishers(context.notion_sync))
        storage.set_run_status(
            context.connection, state["run_id"], "published", _stats(state) | {"outputs": outputs}
        )
        return {"outputs": outputs, "errors": errors}

    def reject(state: WatchState) -> dict:
        storage.set_run_status(context.connection, state["run_id"], "rejected", _stats(state))
        return {}

    def reflect(state: WatchState, store: BaseStore) -> dict:
        """Agent mémoire : consolide les leçons du run dans le Store LangGraph."""
        memory = WatchMemory(store)
        approved = bool(state.get("approval"))
        memory.add_lesson(state.get("note", ""), approved, state["run_id"])
        storage.add_feedback(context.connection, state["run_id"], approved, state.get("note", ""))
        for item in state["digest"]["items"]:
            memory.reinforce_tags(item.get("tags", []), weight=1 if approved else -1)
        if context.claude_memory_path:
            memory.export_markdown(context.claude_memory_path)
        return {"trace": ["mémoire consolidée"]}

    def blocked(state: WatchState) -> dict:
        storage.set_run_status(
            context.connection, state["run_id"], "blocked",
            _stats(state) | {"violations": state["violations"]},
        )
        return {}

    def failed(state: WatchState) -> dict:
        storage.set_run_status(
            context.connection, state["run_id"], "failed", _stats(state) | {"errors": state.get("errors", [])}
        )
        return {}

    builder = StateGraph(WatchState)
    builder.add_node(
        "supervisor",
        supervisor,
        destinations=("collector", "prefilter", "quality", *AGENT_NODES, "approval", "blocked", "failed"),
    )
    builder.add_node("collector", collector)
    builder.add_node("prefilter", prefilter)
    builder.add_node("quality", quality)
    for name, node in zip(AGENT_NODES, (research, review, editorial)):
        builder.add_node(name, node, retry_policy=AGENT_RETRY, error_handler=agent_failed)
    builder.add_node("approval", approval)
    builder.add_node("publish", publish)
    builder.add_node("reject", reject)
    builder.add_node("reflect", reflect)
    builder.add_node("blocked", blocked)
    builder.add_node("failed", failed)

    builder.add_edge(START, "supervisor")
    for worker in ("collector", "prefilter", "quality", *AGENT_NODES):
        builder.add_edge(worker, "supervisor")
    builder.add_conditional_edges(
        "approval", lambda state: "publish" if state.get("approval") else "reject", ["publish", "reject"]
    )
    builder.add_edge("publish", "reflect")
    builder.add_edge("reject", "reflect")
    builder.add_edge("reflect", END)
    builder.add_edge("blocked", END)
    builder.add_edge("failed", END)

    return builder.compile(checkpointer=checkpointer, store=store)


def _stats(state: WatchState) -> dict:
    return {
        "collected": state.get("collected", {}),
        "candidates": len(state.get("candidates", [])),
        "signals": len(state.get("signals", [])),
        "accepted": len(state.get("accepted", [])),
        "errors": len(state.get("errors", [])),
        "trace": state.get("trace", []),
    }


def focus_note(state: WatchState) -> str:
    keywords = (state.get("options") or {}).get("keywords") or []
    return f"- Focus demandé pour cette veille : {', '.join(keywords)}\n" if keywords else ""


def initial_state(run_id: str, plan: TaskGraph, min_relevance: int, options: dict | None = None) -> WatchState:
    return {
        "run_id": run_id,
        "plan": plan.model_dump(),
        "status": {task.id: "pending" for task in plan.tasks},
        "min_relevance": min_relevance,
        "review_round": 0,
        "options": options or {},
    }
