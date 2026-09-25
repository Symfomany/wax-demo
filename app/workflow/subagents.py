"""Sous-agents : sous-graphes LangGraph avec leur propre état.

- quality   : bruit → doublons → sélection (règles déterministes, sans LLM).
- research  : map-reduce — un Scout par lot de documents (Send, parallèle), puis ranking hybride
              et sélection diversifiée.
- review    : pré-contrôle déterministe → Critic LLM (seulement si nécessaire) → décision.
- evidence  : claim_extract (LLM, par lots) → claim_ground → cross_source_verify →
              contradiction_detect → evidence_score (déterministes).
- editorial : Editor (faits, analyse, hypothèse) → guards → boucle de réparation bornée.

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
from app.harness.hooks import document_risks, guard_numbers, run_publish_guards, unsourced_numbers
from app.harness.prompts import render_prompt
from app.llm import BudgetExceeded, LLMOutputError
from app.schemas import (
    Claim,
    ClaimDraft,
    ClaimsOutput,
    Contradiction,
    CriticOutput,
    Critique,
    Digest,
    DigestItem,
    Document,
    EditorOutput,
    Evidence,
    ScoutOutput,
    Signal,
)
from app.workflow.evidence import (
    PROTOCOL_LABELS,
    STATUS_LABELS,
    contradictions,
    corroborations,
    document_text,
    extractive_claim,
    ground_claim,
    is_primary,
    other_documents,
    score_claim,
    signal_confidence,
)
from app.workflow.quality import (
    RankInput,
    cluster_documents,
    diversify,
    hybrid_rank,
    noise_check,
    published_duplicate,
    published_index,
)
from app.workflow.state import HarnessContext


def _date(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d") if value else "non précisée"


def _excerpt(document: Document, limit: int) -> str:
    return sanitize_untrusted(document.summary, limit)


# --- Quality : bruit → doublons → sélection ------------------------------------


class QualityState(TypedDict, total=False):
    candidates: list[dict]
    exclusions: list[str]
    published: list[list[str]]  # [url, titre] des items déjà publiés
    max_documents: int
    kept: list[dict]
    flags: dict[str, list[str]]
    filtered: Annotated[list[dict], operator.add]
    quality: dict[str, dict]


def build_quality(context: HarnessContext):
    def noise(state: QualityState) -> dict:
        kept, flags, filtered = [], {}, []
        for item in state["candidates"]:
            document = Document.model_validate(item)
            reason, found = noise_check(document, state.get("exclusions", []))
            if reason:
                filtered.append({"url": item["url"], "title": document.title, "kind": "bruit", "reason": reason})
                continue
            kept.append(item)
            if found:
                flags[item["url"]] = found
        return {"kept": kept, "flags": flags, "filtered": filtered}

    def dedup(state: QualityState) -> dict:
        published = published_index([(url, title) for url, title in state.get("published", [])])
        documents, filtered = [], []
        for item in state["kept"]:
            document = Document.model_validate(item)
            if title := published_duplicate(document, published, context.dedup_threshold):
                filtered.append({"url": item["url"], "title": document.title, "kind": "doublon",
                                 "reason": f"déjà publié : « {title[:90]} »"})
            else:
                documents.append(document)
        quality, kept = {}, []
        for cluster in cluster_documents(documents, context.dedup_threshold):
            url = str(cluster.representative.url)
            kept.append(cluster.representative.model_dump(mode="json"))
            quality[url] = {"sources": len(cluster.sources), "flags": state.get("flags", {}).get(url, []),
                            "duplicates": [str(d.url) for d in cluster.members]}
            filtered += [{"url": str(d.url), "title": d.title, "kind": "doublon",
                          "reason": f"doublon de « {cluster.representative.title[:90]} »"} for d in cluster.members]
        return {"kept": kept, "quality": quality, "filtered": filtered}

    def select(state: QualityState) -> dict:
        # Le pool du prefilter est déjà équilibré par source (tourniquet) : on garde sa tête.
        kept = state["kept"][: state.get("max_documents") or context.max_documents]
        return {"kept": kept, "quality": {item["url"]: state["quality"][item["url"]] for item in kept}}

    builder = StateGraph(QualityState)
    builder.add_node("noise", noise)
    builder.add_node("dedup", dedup)
    builder.add_node("select", select)
    builder.add_edge(START, "noise")
    builder.add_edge("noise", "dedup")
    builder.add_edge("dedup", "select")
    builder.add_edge("select", END)
    return builder.compile()


# --- Research : map-reduce de Scouts ---------------------------------------


class ResearchState(TypedDict, total=False):
    candidates: list[dict]
    min_relevance: int
    memory: str
    quality: dict[str, dict]  # sous-graphe quality : corroboration et bruit léger par URL
    keywords: list[str]  # profil Grill-me + focus du run + thèmes appris : composante « profil »
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
        """Ranking hybride explicable : score LLM + fraîcheur + profil + source + corroboration."""
        documents = [Document.model_validate(item) for item in state["candidates"]]
        quality = state.get("quality") or {}
        inputs: dict[str, RankInput] = {}
        for pick in state.get("picks", []):
            if pick["relevance"] < state["min_relevance"]:
                continue
            document = documents[pick["index"]]
            if str(document.url) in inputs:
                continue
            info = quality.get(str(document.url), {})
            signal = Signal(
                title=document.title,
                url=document.url,
                source=document.source,
                published_at=document.published_at,
                relevance=pick["relevance"],
                novelty=pick["novelty"],
                confidence=pick["confidence"],
                why_it_matters=pick["why_it_matters"],
                tags=[*document.tags[:2], *pick["tags"][:3]],
            )
            inputs[str(document.url)] = RankInput(signal, document, info.get("sources", 1), info.get("flags", []))
        ranked = hybrid_rank(list(inputs.values()), context.now(), context.max_age_days,
                             state.get("keywords", []), context.rank_weights, context.profile)
        # Sélection diversifiée : pas six signaux du même flux ou du même thème.
        selected = diversify(ranked, context.max_signals, context.diversity_penalty)
        return {"signals": [signal.model_dump(mode="json") for signal in selected]}

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


# --- Evidence : claims → ancrage → corroboration → contradictions → score ---------------


class EvidenceState(TypedDict, total=False):
    candidates: list[dict]
    accepted: list[dict]
    others: list[dict]  # doublons écartés par quality, relus en SQLite pour la corroboration
    drafts: list[dict]
    errors: Annotated[list[str], operator.add]
    claims: list[dict]
    corroborating: dict[str, list[dict]]
    contradicting: dict[str, list[str]]
    contradictions: list[dict]
    evidence: dict[str, dict]


def build_evidence(context: HarnessContext):
    def sources(state: EvidenceState) -> tuple[list[Signal], dict[str, Document]]:
        documents = {item["url"]: Document.model_validate(item) for item in state["candidates"]}
        return [Signal.model_validate(item) for item in state["accepted"]], documents

    def claim_extract(state: EvidenceState) -> dict:
        """Un appel LLM par lot de signaux (un seul à la fois : Jetson) ; un lot en échec est ignoré."""
        signals, documents = sources(state)
        drafts, errors = [], []
        size = context.evidence_batch_size
        for start in range(0, len(signals), size):
            listing = "\n\n".join(
                f"[signal_id={index}] {signal.title} ({signal.source}, {_date(signal.published_at)})\n"
                f"Extrait : {document_text(documents[str(signal.url)], context.evidence_excerpt_chars)}"
                for index, signal in enumerate(signals[start : start + size], start=start + 1)
            )
            try:
                output = context.llm.generate(
                    render_prompt("claims", signals=listing, max_claims=context.evidence_max_claims), ClaimsOutput
                )
            except (LLMOutputError, BudgetExceeded) as error:
                errors.append(f"evidence lot {start // size + 1} : {error}")
                continue
            per_signal: dict[int, int] = {}
            for draft in output.claims:
                # Guard : un signal_id hors du lot est ignoré ; claims bornés par signal.
                if start < draft.signal_id <= start + size and draft.signal_id <= len(signals):
                    per_signal[draft.signal_id] = per_signal.get(draft.signal_id, 0) + 1
                    if per_signal[draft.signal_id] <= context.evidence_max_claims:
                        drafts.append(draft.model_dump())
        return {"drafts": drafts, "errors": errors}

    def claim_ground(state: EvidenceState) -> dict:
        """Citation retrouvée mot pour mot, chiffres de l'affirmation présents dans la citation ;
        un signal sans aucune affirmation étayée reçoit un fait extractif (première phrase citée)."""
        signals, documents = sources(state)
        claims: list[Claim] = []
        for index, signal in enumerate(signals, start=1):
            document = documents[str(signal.url)]
            text = document_text(document, context.evidence_excerpt_chars)
            primary = is_primary(document, context.secondary_sources)
            mine = [ClaimDraft.model_validate(d) for d in state.get("drafts", []) if d["signal_id"] == index]
            grounded = []
            for number, draft in enumerate(mine, start=1):
                claim = ground_claim(f"S{index}-C{number}", draft, document, text, primary)
                if claim.status != "non_etaye" and (extra := unsourced_numbers(claim.text, claim.evidence[0].quote)):
                    claim = claim.model_copy(update={"status": "non_etaye", "evidence": [],
                                                     "reasons": [f"chiffre absent de la citation : {', '.join(extra)}"]})
                grounded.append(claim)
            if not any(c.status != "non_etaye" for c in grounded):
                if fallback := extractive_claim(f"S{index}-C0", document, text, primary):
                    grounded.append(fallback)
            claims += grounded
        return {"claims": [claim.model_dump(mode="json") for claim in claims]}

    def others(state: EvidenceState) -> list:
        documents = [Document.model_validate(item) for item in [*state["candidates"], *state.get("others", [])]]
        return other_documents(documents, context.evidence_excerpt_chars, context.secondary_sources)

    def cross_source_verify(state: EvidenceState) -> dict:
        pool = others(state)
        corroborating = {}
        for item in state["claims"]:
            claim = Claim.model_validate(item)
            if claim.status != "non_etaye":
                corroborating[claim.id] = [e.model_dump(mode="json") for e in corroborations(claim, pool)]
        return {"corroborating": corroborating}

    def contradiction_detect(state: EvidenceState) -> dict:
        pool = others(state)
        found, contradicting = [], {}
        for item in state["claims"]:
            claim = Claim.model_validate(item)
            for other, sentence in contradictions(claim, pool):
                contradicting.setdefault(claim.id, []).append(str(other.document.url))
                found.append(Contradiction(
                    claim_id=claim.id, claim=claim.text, quote=claim.evidence[0].quote,
                    other_url=other.document.url, other_quote=sentence,
                    reason="même sujet, chiffres incompatibles",
                ).model_dump(mode="json"))
        return {"contradicting": contradicting, "contradictions": found}

    def evidence_score(state: EvidenceState) -> dict:
        scored: list[Claim] = []
        for item in state["claims"]:
            claim = Claim.model_validate(item)
            against = state.get("contradicting", {}).get(claim.id, [])
            support = [Evidence.model_validate(e) for e in state.get("corroborating", {}).get(claim.id, [])
                       if e["url"] not in against]
            scored.append(score_claim(claim, support, len(against)).model_copy(update={"contradicts": against}))
        evidence = {}
        for signal in [Signal.model_validate(item) for item in state["accepted"]]:
            mine = [c for c in scored if str(c.signal_url) == str(signal.url)]
            dropped = sum(1 for c in mine if c.status == "non_etaye")
            confidence, reasons = signal_confidence(mine, dropped)
            evidence[str(signal.url)] = {"confidence": confidence, "reasons": reasons}
        return {"claims": [claim.model_dump(mode="json") for claim in scored], "evidence": evidence}

    builder = StateGraph(EvidenceState)
    for name, node in (("claim_extract", claim_extract), ("claim_ground", claim_ground),
                       ("cross_source_verify", cross_source_verify), ("contradiction_detect", contradiction_detect),
                       ("evidence_score", evidence_score)):
        builder.add_node(name, node)
    builder.add_edge(START, "claim_extract")
    builder.add_edge("claim_extract", "claim_ground")
    builder.add_edge("claim_ground", "cross_source_verify")
    builder.add_edge("cross_source_verify", "contradiction_detect")
    builder.add_edge("contradiction_detect", "evidence_score")
    builder.add_edge("evidence_score", END)
    return builder.compile()


# --- Editorial : Editor → guards → réparation bornée -----------------------------


class EditorialState(TypedDict, total=False):
    candidates: list[dict]
    accepted: list[dict]
    rejected_count: int
    memory: str
    claims: list[dict]
    contradictions: list[dict]
    evidence: dict[str, dict]
    digest: dict
    violations: list[str]
    repair_round: int


def _facts_listing(facts: list[Claim], contradictions: list[dict]) -> str:
    """Faits structurés transmis à l'Editor : statut, confiance, citation, protocole manquant."""
    if not facts:
        return "Faits vérifiés : aucun (s'en tenir à l'extrait)."
    lines = ["Faits vérifiés :"]
    for fact in facts:
        missing = (f" ; protocole incomplet : {', '.join(PROTOCOL_LABELS[m] for m in fact.missing_protocol)}"
                   if fact.missing_protocol else "")
        lines.append(f"- [{STATUS_LABELS[fact.status]}, {fact.kind}, {fact.confidence}/100{missing}] "
                     f"{fact.text} — « {fact.evidence[0].quote} »")
    ids = {fact.id for fact in facts}
    for item in contradictions:
        if item["claim_id"] in ids:
            lines.append(f"- CONTRADICTION : « {item['quote']} » contre « {item['other_quote']} »")
    return "\n".join(lines)


def build_editorial(context: HarnessContext):
    def write(state: EditorialState, corrections: str = "Aucune.") -> dict:
        now = context.now()
        documents = {item["url"]: Document.model_validate(item) for item in state["candidates"]}
        accepted = [Signal.model_validate(item) for item in state["accepted"]]
        claims = [Claim.model_validate(item) for item in state.get("claims", [])]
        evidence = state.get("evidence", {})
        contradictions_ = state.get("contradictions", [])
        output = EditorOutput(executive_summary="Aucun signal validé sur la période.", items=[])

        # Seuls les faits étayés atteignent l'Editor et le digest, les plus sûrs d'abord.
        facts = {
            str(signal.url): sorted((c for c in claims if str(c.signal_url) == str(signal.url)
                                     and c.status != "non_etaye"), key=lambda c: -c.confidence)
            for signal in accepted
        }
        if accepted:
            listing = "\n\n".join(
                f"[signal_id={index}] {signal.title} ({signal.source}, {_date(signal.published_at)})\n"
                f"Pourquoi (Scout) : {signal.why_it_matters}\n"
                + (f"Pour toi : {'; '.join(signal.impact_reasons)}\n" if signal.impact_reasons else "")
                + f"{_facts_listing(facts[str(signal.url)], contradictions_)}\n"
                f"Extrait source : {_excerpt(documents[str(signal.url)], 400 if facts[str(signal.url)] else 700)}"
                for index, signal in enumerate(accepted, start=1)
            )
            output = context.llm.generate(
                render_prompt(
                    "editor", signals=listing, memory=state.get("memory", "Aucun."),
                    corrections=corrections,
                    profile=context.profile.prompt_text() if context.profile else "Aucun profil déclaré.",
                ),
                EditorOutput,
            )

        written = {item.signal_id: item for item in output.items}
        items = []
        for index, signal in enumerate(accepted, start=1):
            text = written.get(index)
            proof = evidence.get(str(signal.url), {})
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
                    score=signal.score,
                    rank_reasons=signal.rank_reasons,
                    # Faits recopiés par le code depuis le sous-graphe evidence, jamais par le LLM.
                    facts=facts[str(signal.url)],
                    analysis=text.analysis.strip() if text else "",
                    hypothesis=text.hypothesis.strip() if text else "",
                    confidence=proof.get("confidence"),
                    confidence_reasons=proof.get("reasons", []),
                    impact_reasons=signal.impact_reasons,
                )
            )
        digest = Digest(
            generated_at=now,
            period_label=f"Veille du {now:%d/%m/%Y}",
            executive_summary=output.executive_summary,
            items=items,
            rejected_count=state.get("rejected_count", 0),
            contradictions=[c for c in contradictions_
                            if any(c["claim_id"] == f.id for fs in facts.values() for f in fs)],
            profile_version=context.profile.label if context.profile else None,
        )
        return {"digest": digest.model_dump(mode="json")}

    def editor(state: EditorialState) -> dict:
        return write(state) | {"repair_round": 0}

    def guards(state: EditorialState) -> dict:
        known_urls = {item["url"] for item in state["candidates"]}
        digest = Digest.model_validate(state["digest"])
        texts = {}
        for item in state["candidates"]:
            document = Document.model_validate(item)
            texts[item["url"]] = f"{document.title}\n{document.summary}\n{document.content}"
        return {"violations": [*run_publish_guards(digest, known_urls, context.now()),
                               *guard_numbers(digest, texts)]}

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
