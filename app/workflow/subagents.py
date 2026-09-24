"""Sous-agents : sous-graphes LangGraph avec leur propre état.

- research  : map-reduce — un Scout par lot de documents (Send, parallèle), puis classement.
- review    : pré-contrôle déterministe → Critic LLM (seulement si nécessaire) → décision.
- editorial : Editor → guards → boucle de réparation conditionnelle bornée.

Chaque sous-graphe a son propre schéma d'état : il ne renvoie au graphe parent
que ce que le nœud d'adaptation en extrait (pas de fuite de clés à réducteur).
"""

from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.harness.guards import sanitize_untrusted
from app.harness.hooks import document_risks, run_publish_guards
from app.harness.prompts import render_prompt
from app.llm import BudgetExceeded, LLMOutputError
from app.schemas import (
    CriticOutput,
    Critique,
    Digest,
    DigestItem,
    Document,
    EditorOutput,
    ScoutOutput,
    Signal,
)
from app.workflow.state import HarnessContext


def _date(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value else "non précisée"


def _excerpt(document: Document, limit: int) -> str:
    return sanitize_untrusted(document.summary, limit)


# --- Research : map-reduce de Scouts ---------------------------------------


class ResearchState(TypedDict, total=False):
    candidates: list[dict]
    min_relevance: int
    memory: str
    picks: Annotated[list[dict], operator.add]
    errors: Annotated[list[str], operator.add]
    signals: list[dict]


class ScoutBatch(TypedDict):
    start: int
    batch: list[dict]
    memory: str


def build_research(context: HarnessContext):
    def fan_out(state: ResearchState):
        candidates = state["candidates"]
        if not candidates:
            return "rank"
        return [
            Send(
                "scout_batch",
                {"start": start, "batch": candidates[start : start + context.batch_size],
                 "memory": state.get("memory", "Aucun.")},
            )
            for start in range(0, len(candidates), context.batch_size)
        ]

    def scout_batch(task: ScoutBatch) -> dict:
        documents = [Document.model_validate(item) for item in task["batch"]]
        start = task["start"]
        listing = "\n\n".join(
            f"[{start + offset}] {doc.title}\n"
            f"Source : {doc.source} — {', '.join(doc.tags[1:2])}\n"
            f"Date : {_date(doc.published_at)}\n"
            f"Extrait : {_excerpt(doc, 600)}"
            for offset, doc in enumerate(documents, start=1)
        )
        try:
            output = context.llm.generate(
                render_prompt(
                    "scout",
                    criteria=context.skill.section("Critères de priorité"),
                    memory=task["memory"],
                    max_picks=max(2, context.batch_size // 2),
                    documents=listing,
                ),
                ScoutOutput,
            )
        except (LLMOutputError, BudgetExceeded) as error:
            # Guard : un lot en échec est ignoré, les autres lots continuent.
            return {"errors": [f"scout lot {start // context.batch_size + 1} : {error}"]}

        picks = []
        for pick in output.picks:
            index = pick.doc_id - 1
            # Guard : un doc_id hors du lot est ignoré, jamais « deviné ».
            if start <= index < start + len(documents):
                picks.append(pick.model_dump() | {"index": index})
        return {"picks": picks}

    def rank(state: ResearchState) -> dict:
        documents = [Document.model_validate(item) for item in state["candidates"]]
        signals: dict[str, Signal] = {}
        for pick in state.get("picks", []):
            if pick["relevance"] < state["min_relevance"]:
                continue
            document = documents[pick["index"]]
            signals.setdefault(
                str(document.url),
                Signal(
                    title=document.title,
                    url=document.url,
                    source=document.source,
                    published_at=document.published_at,
                    relevance=pick["relevance"],
                    novelty=pick["novelty"],
                    confidence=pick["confidence"],
                    why_it_matters=pick["why_it_matters"],
                    tags=[*document.tags[:2], *pick["tags"][:3]],
                ),
            )
        ranked = sorted(
            signals.values(),
            key=lambda s: (2 * s.relevance + s.novelty + s.confidence),
            reverse=True,
        )[: context.max_signals]
        return {"signals": [signal.model_dump(mode="json") for signal in ranked]}

    builder = StateGraph(ResearchState)
    builder.add_node("scout_batch", scout_batch)
    builder.add_node("rank", rank)
    builder.add_conditional_edges(START, fan_out, ["scout_batch", "rank"])
    builder.add_edge("scout_batch", "rank")
    builder.add_edge("rank", END)
    return builder.compile()


# --- Review : pré-contrôle → Critic → décision --------------------------------


class ReviewState(TypedDict, total=False):
    candidates: list[dict]
    signals: list[dict]
    risks: dict[str, list[str]]
    pending: list[int]
    verdicts: dict[str, dict]
    critiques: list[dict]
    accepted: list[dict]


def build_review(context: HarnessContext):
    def precheck(state: ReviewState) -> dict:
        now = context.now()
        documents = {item["url"]: Document.model_validate(item) for item in state["candidates"]}
        risks, pending, verdicts = {}, [], {}
        for index, item in enumerate(state["signals"], start=1):
            found = document_risks(documents[item["url"]], now, context.max_age_days)
            risks[str(index)] = found
            if "date de publication dans le futur" in found:
                # Guard bloquant : aucune voix du LLM ne peut le lever.
                verdicts[str(index)] = {"verdict": "drop", "rationale": "Date future (contrôle automatique)."}
            else:
                pending.append(index)
        return {"risks": risks, "pending": pending, "verdicts": verdicts}

    def route(state: ReviewState) -> str:
        return "critic" if state["pending"] else "decide"

    def critic(state: ReviewState) -> dict:
        documents = {item["url"]: Document.model_validate(item) for item in state["candidates"]}
        signals = {index: Signal.model_validate(state["signals"][index - 1]) for index in state["pending"]}
        listing = "\n\n".join(
            f"[signal_id={index}] {signal.title}\n"
            f"Source : {signal.source} — date {_date(signal.published_at)}\n"
            f"Justification Scout : {signal.why_it_matters}\n"
            f"Extrait source : {_excerpt(documents[str(signal.url)], 700)}\n"
            f"Risques détectés automatiquement : {', '.join(state['risks'][str(index)]) or 'aucun'}"
            for index, signal in signals.items()
        )
        output = context.llm.generate(render_prompt("critic", signals=listing), CriticOutput)
        verdicts = dict(state["verdicts"])
        for verdict in output.verdicts:
            if verdict.signal_id in signals:
                verdicts[str(verdict.signal_id)] = verdict.model_dump()
        return {"verdicts": verdicts}

    def decide(state: ReviewState) -> dict:
        critiques, accepted = [], []
        for index, item in enumerate(state["signals"], start=1):
            verdict = state["verdicts"].get(str(index))
            critique = Critique(
                signal_url=item["url"],
                verdict=verdict["verdict"] if verdict else "needs_review",
                rationale=verdict["rationale"] if verdict else "Aucun verdict rendu par Critic.",
                factual_risks=[*state["risks"][str(index)], *(verdict or {}).get("factual_risks", [])],
            )
            critiques.append(critique.model_dump(mode="json"))
            if critique.verdict == "keep":
                accepted.append(item)
        return {"critiques": critiques, "accepted": accepted}

    builder = StateGraph(ReviewState)
    builder.add_node("precheck", precheck)
    builder.add_node("critic", critic)
    builder.add_node("decide", decide)
    builder.add_edge(START, "precheck")
    builder.add_conditional_edges("precheck", route, ["critic", "decide"])
    builder.add_edge("critic", "decide")
    builder.add_edge("decide", END)
    return builder.compile()


# --- Editorial : Editor → guards → réparation bornée -----------------------------


class EditorialState(TypedDict, total=False):
    candidates: list[dict]
    accepted: list[dict]
    rejected_count: int
    memory: str
    digest: dict
    violations: list[str]
    repair_round: int


def build_editorial(context: HarnessContext):
    def write(state: EditorialState, corrections: str = "Aucune.") -> dict:
        now = context.now()
        documents = {item["url"]: Document.model_validate(item) for item in state["candidates"]}
        accepted = [Signal.model_validate(item) for item in state["accepted"]]
        output = EditorOutput(executive_summary="Aucun signal validé sur la période.", items=[])

        if accepted:
            listing = "\n\n".join(
                f"[signal_id={index}] {signal.title} ({signal.source}, {_date(signal.published_at)})\n"
                f"Pourquoi (Scout) : {signal.why_it_matters}\n"
                f"Extrait source : {_excerpt(documents[str(signal.url)], 700)}"
                for index, signal in enumerate(accepted, start=1)
            )
            output = context.llm.generate(
                render_prompt(
                    "editor", signals=listing, memory=state.get("memory", "Aucun."),
                    corrections=corrections,
                ),
                EditorOutput,
            )

        written = {item.signal_id: item for item in output.items}
        items = []
        for index, signal in enumerate(accepted, start=1):
            text = written.get(index)
            items.append(
                DigestItem(
                    title=signal.title,
                    source=signal.source,
                    url=signal.url,
                    date=signal.published_at,
                    # Repli extractif si l'Editor a omis ce signal.
                    summary=text.summary if text else documents[str(signal.url)].summary[:300],
                    why_it_matters=text.why_it_matters if text else signal.why_it_matters,
                    tags=signal.tags,
                )
            )
        digest = Digest(
            generated_at=now,
            period_label=f"Veille du {now:%d/%m/%Y}",
            executive_summary=output.executive_summary,
            items=items,
            rejected_count=state.get("rejected_count", 0),
        )
        return {"digest": digest.model_dump(mode="json")}

    def editor(state: EditorialState) -> dict:
        return write(state) | {"repair_round": 0}

    def guards(state: EditorialState) -> dict:
        known_urls = {item["url"] for item in state["candidates"]}
        digest = Digest.model_validate(state["digest"])
        return {"violations": run_publish_guards(digest, known_urls, context.now())}

    def route(state: EditorialState) -> str:
        repairable = [v for v in state["violations"] if not v.startswith("Digest vide")]
        if repairable and state["repair_round"] < context.max_repair_rounds:
            return "repair"
        return END

    def repair(state: EditorialState) -> dict:
        corrections = "\n".join(f"- {violation}" for violation in state["violations"])
        return write(state, corrections) | {"repair_round": state["repair_round"] + 1}

    builder = StateGraph(EditorialState)
    builder.add_node("editor", editor)
    builder.add_node("guards", guards)
    builder.add_node("repair", repair)
    builder.add_edge(START, "editor")
    builder.add_edge("editor", "guards")
    builder.add_conditional_edges("guards", route, ["repair", END])
    builder.add_edge("repair", "guards")
    return builder.compile()
