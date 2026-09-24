"""Veilles lancées depuis l'interface : exécution en arrière-plan et événements de progression.

Une seule veille à la fois (GPU local). L'interface interroge les événements
(`events(after=n)`) ; la validation humaine reprend le graphe par Command(resume).
"""

from __future__ import annotations

import threading
import time
import traceback
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from langgraph.types import Command

from app import storage
from app.workflow.graph import initial_state
from app.workflow.tasks import COLLECTORS, default_plan

# (graph, context) prêts à l'emploi pour un run ; fermés à la fin du thread.
GraphFactory = Callable[[], AbstractContextManager]
TERMINAL = {"published", "rejected", "blocked", "failed", "error"}


@dataclass
class RunRecord:
    id: str
    status: str = "running"
    events: list[dict] = field(default_factory=list)
    markdown: str = ""
    outputs: dict = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    options: dict = field(default_factory=dict)
    steps: list[dict] = field(default_factory=list)

    def public(self, after: int = 0) -> dict:
        return {"id": self.id, "status": self.status, "markdown": self.markdown, "options": self.options,
                "outputs": self.outputs, "started_at": self.started_at,
                "events": self.events[after:], "next": len(self.events)}


def translate(chunk: dict) -> list[dict]:
    """Mises à jour LangGraph (stream_mode="updates") → événements lisibles."""
    events = []
    for node, update in chunk.items():
        if node == "__interrupt__":
            events.append({"type": "awaiting_approval"})
            continue
        update = update or {}
        for line in update.get("trace", []):
            events.append({"type": "step", "node": node, "text": line})
        for source, count in update.get("collected", {}).items():
            events.append({"type": "collect", "source": source, "count": count})
        for error in update.get("errors", []):
            events.append({"type": "warning", "text": error})
        if node == "research":
            events.append({"type": "agent", "node": node, "text": f"{len(update.get('signals', []))} signal(aux) proposé(s)"})
        elif node == "review":
            events.append({"type": "agent", "node": node, "text": f"{len(update.get('accepted', []))} signal(aux) accepté(s)"})
        elif node == "editorial":
            events.append({"type": "agent", "node": node, "text": "digest rédigé"
                           + (f", {len(update['violations'])} violation(s)" if update.get("violations") else "")})
        elif node == "publish":
            events.append({"type": "published", "outputs": update.get("outputs", {})})
    return events


class RunManager:
    def __init__(self, graph_factory: GraphFactory, min_relevance: int = 6) -> None:
        self.graph_factory = graph_factory
        self.min_relevance = min_relevance
        self.runs: dict[str, RunRecord] = {}
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    # --- API ------------------------------------------------------------------

    def start(self, options: dict | None = None) -> str:
        options = options or {}
        with self._lock:
            if any(run.status == "running" for run in self.runs.values()):
                raise RuntimeError("Une veille est déjà en cours : attendez sa fin avant d'en relancer une.")
            record = RunRecord(id=str(uuid4()))
            self.runs[record.id] = record
        wanted = options.get("sources") or list(COLLECTORS)
        collectors = tuple(
            name for name in COLLECTORS
            if name in wanted and (options.get("mcp", True) or name != "github_mcp")
        )
        plan = default_plan(collect=options.get("collect", True), collectors=collectors)
        run_options = {key: options[key] for key in ("keywords", "match_all", "max_age_days", "max_documents")
                       if options.get(key)}
        record.options = run_options | {"sources": list(collectors)}
        payload = initial_state(record.id, plan, self.min_relevance, run_options)
        self._spawn(record, payload)
        return record.id

    def resume(self, run_id: str, approved: bool, note: str = "") -> None:
        record = self.get(run_id)
        if record.status != "awaiting_approval":
            raise RuntimeError(f"Le run {run_id[:8]} n'attend pas de validation ({record.status}).")
        record.status = "running"
        self._emit(record, {"type": "decision", "approved": approved, "note": note})
        self._spawn(record, Command(resume={"approved": approved, "note": note}))

    def get(self, run_id: str) -> RunRecord:
        if run_id not in self.runs:
            raise KeyError(run_id)
        return self.runs[run_id]

    def list(self) -> list[dict]:
        return [
            {"id": r.id, "status": r.status, "started_at": r.started_at}
            for r in sorted(self.runs.values(), key=lambda r: r.started_at, reverse=True)
        ]

    def wait(self, run_id: str, timeout: float = 30) -> None:
        """Pour les tests : attend la fin du thread courant du run."""
        thread = self._threads.get(run_id)
        if thread:
            thread.join(timeout)

    # --- Exécution -------------------------------------------------------------

    def _emit(self, record: RunRecord, event: dict) -> None:
        with self._lock:
            record.events.append(event | {"at": datetime.now(timezone.utc).strftime("%H:%M:%S")})

    def _spawn(self, record: RunRecord, payload) -> None:
        thread = threading.Thread(target=self._execute, args=(record, payload), daemon=True)
        self._threads[record.id] = thread
        thread.start()

    def _execute(self, record: RunRecord, payload) -> None:
        config = {"configurable": {"thread_id": record.id}, "recursion_limit": 60}
        try:
            with self.graph_factory() as (graph, context):
                if not isinstance(payload, Command):
                    storage.set_run_status(context.connection, record.id, "running")
                last = time.perf_counter()
                for namespace, chunk in graph.stream(
                    payload, config | context_trace(record.id), stream_mode="updates", subgraphs=True
                ):
                    now = time.perf_counter()
                    # sous-graphes : espace de noms « research:<id> » → graphe « research »
                    graph_name = namespace[0].split(":")[0] if namespace else "veille"
                    for node, update in chunk.items():
                        record.steps.append({"graph": graph_name, "node": node,
                                             "ms": round((now - last) * 1000),
                                             "detail": run_step_detail(node, update or {})})
                    last = now
                    if not namespace:
                        for event in translate(chunk):
                            self._emit(record, event)
                snapshot = graph.get_state(config)
                values = snapshot.values
                if snapshot.next:
                    record.markdown = snapshot.tasks[0].interrupts[0].value["markdown"]
                    record.status = "awaiting_approval"
                    storage.set_run_status(context.connection, record.id, "awaiting_approval")
                elif values.get("outputs"):
                    record.outputs = values["outputs"]
                    record.status = "published"
                elif values.get("violations"):
                    record.status = "blocked"
                    for violation in values["violations"]:
                        self._emit(record, {"type": "warning", "text": violation})
                elif values.get("approval") is False:
                    record.status = "rejected"
                else:
                    record.status = "failed"
                storage.save_trace(
                    context.connection, record.id, "veille", record.id,
                    "Veille" + (f" ciblée : {', '.join(record.options.get('keywords', []))}"
                                if record.options.get("keywords") else ""),
                    record.steps, run_engagement(record),
                )
        except Exception as error:  # noqa: BLE001 — remonté dans l'interface
            record.status = "error"
            self._emit(record, {"type": "error", "text": f"{type(error).__name__} : {error}"})
            traceback.print_exc()
        self._emit(record, {"type": "status", "status": record.status})


def run_step_detail(node: str, update: dict) -> str:
    if node == "__interrupt__":
        return "validation humaine attendue"
    if update.get("trace"):
        return update["trace"][0]
    if "collected" in update:
        return ", ".join(f"{source} +{count}" for source, count in update["collected"].items())
    if "signals" in update:
        return f"{len(update['signals'])} signal(aux)"
    if "accepted" in update:
        return f"{len(update['accepted'])} accepté(s)"
    if "violations" in update:
        return f"{len(update['violations'])} violation(s)"
    if "picks" in update:
        return f"{len(update['picks'])} choix du Scout"
    if update.get("errors"):
        return update["errors"][0]
    return ""


AGENT_OF_NODE = {"scout_batch": "scout", "critic": "critic", "editor": "editor", "repair": "editor",
                 "collector": "collector", "supervisor": "supervisor", "reflect": "reflect"}
PROMPT_OF_NODE = {"scout_batch": "scout.md", "critic": "critic.md", "editor": "editor.md", "repair": "editor.md"}


def run_engagement(record: RunRecord) -> dict:
    nodes = [step["node"] for step in record.steps]
    agents = list(dict.fromkeys(AGENT_OF_NODE[n] for n in nodes if n in AGENT_OF_NODE))
    sources = record.options.get("sources", [])
    return {
        "nodes": list(dict.fromkeys(nodes)),
        "agents": agents,
        "skills": ["veille-tech"] if "scout" in agents else [],
        "mcp": (["github-scout"] if "github_mcp" in sources else [])
        + (["notion-veille"] if record.outputs.get("notion") else []),
        "prompts": list(dict.fromkeys(PROMPT_OF_NODE[n] for n in nodes if n in PROMPT_OF_NODE)),
        "options": record.options,
    }


def context_trace(run_id: str) -> dict:
    from app.observability import trace_config

    return trace_config(run_id, "veille", trace_seed=run_id)
