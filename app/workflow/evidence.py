"""Claims et preuves : ancrage, corroboration, contradictions et score de confiance.

Le LLM (claim_extract) propose des affirmations avec une citation ; tout le reste est
déterministe, pour que la confiance affichée ne dépende pas du modèle :

- ancrage : la citation doit figurer mot pour mot dans l'extrait de la source, sinon
  l'affirmation est « non étayée » et n'est jamais publiée comme fait ;
- benchmark : chaque champ du protocole (matériel, modèle, batch, contexte, version,
  méthode) doit aussi figurer dans la source ; un protocole incomplet empêche « confirmé » ;
- source secondaire (sources.toml, `primary = false`) : jamais « confirmé » sans une
  source primaire qui corrobore ;
- corroboration : une autre source (hôte différent) reprend les chiffres et l'essentiel
  des mots de la citation ;
- contradiction : une phrase d'une autre source parle de la même chose (mots communs)
  avec des chiffres incompatibles ; elle est affichée, jamais masquée par la synthèse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.harness.guards import sanitize_untrusted
from app.review import quote_in_text
from app.schemas import FACT_KINDS, PROTOCOL_FIELDS, BenchmarkProtocol, Claim, ClaimDraft, Document, Evidence
from app.workflow.quality import title_tokens

PROTOCOL_LABELS = {"hardware": "matériel", "model": "modèle", "batch": "batch", "context": "contexte",
                   "version": "version", "method": "méthode"}
STATUS_LABELS = {"confirme": "confirmé", "rapporte": "rapporté", "conteste": "contesté", "non_etaye": "non étayé"}
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def document_text(document: Document, limit: int) -> str:
    """Extrait vu par le LLM et référence de l'ancrage (consignes injectées neutralisées)."""
    text = document.summary if document.content.strip() in document.summary else f"{document.summary}\n{document.content}"
    return sanitize_untrusted(text.strip(), limit)


def is_primary(document: Document, secondary: set[str]) -> bool:
    """Source primaire sauf flux déclaré `primary = false` dans sources.toml (nom du flux = 2e tag)."""
    return not (len(document.tags) > 1 and document.tags[1].lower() in secondary)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("’", "'")).strip()


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def measures(tokens: frozenset[str]) -> frozenset[str]:
    return frozenset(t for t in tokens if any(c.isdigit() for c in t))


def words(tokens: frozenset[str]) -> frozenset[str]:
    return tokens - measures(tokens)


def verify_protocol(protocol: BenchmarkProtocol | None, text: str) -> tuple[BenchmarkProtocol, list[str]]:
    """Garde les champs retrouvés dans la source ; renvoie les champs manquants."""
    haystack = _norm(text)
    kept = {name: value.strip() for name, value in (protocol or BenchmarkProtocol()).model_dump().items()
            if value.strip() and _norm(value) in haystack}
    return BenchmarkProtocol(**kept), [name for name in PROTOCOL_FIELDS if name not in kept]


def ground_claim(claim_id: str, draft: ClaimDraft, document: Document, text: str, primary: bool) -> Claim:
    """Citation retrouvée dans l'extrait → preuve ; sinon « non étayé »."""
    quote = draft.quote.strip()[:300]
    claim = Claim(id=claim_id, signal_url=document.url, text=draft.text.strip(), kind=draft.kind)
    if not quote or not quote_in_text(quote, text):
        return claim.model_copy(update={"reasons": ["citation introuvable dans la source"]})
    update: dict = {"status": "rapporte",
                    "evidence": [Evidence(url=document.url, source=document.source, quote=quote, primary=primary)]}
    if draft.kind == "benchmark":
        update["benchmark"], update["missing_protocol"] = verify_protocol(draft.benchmark, text)
    return claim.model_copy(update=update)


def extractive_claim(claim_id: str, document: Document, text: str, primary: bool) -> Claim | None:
    """Repli sans LLM : la première phrase exploitable de la source, citée telle quelle."""
    sentence = next((s.strip() for s in _SENTENCE.split(text) if len(s.strip()) >= 30), "")
    if not sentence:
        return None
    quote = sentence[:250]
    return Claim(id=claim_id, signal_url=document.url, text=quote, kind="annonce", status="rapporte",
                 evidence=[Evidence(url=document.url, source=document.source, quote=quote, primary=primary)],
                 reasons=["extrait de la source (repli sans LLM)"])


@dataclass
class Other:
    """Autre document du pool (candidat ou doublon écarté) pour la corroboration."""

    document: Document
    text: str
    primary: bool


def corroborations(claim: Claim, others: list[Other]) -> list[Evidence]:
    """Sources d'autres hôtes qui reprennent les chiffres et ≥ 50 % des mots de la citation."""
    if not claim.evidence:
        return []
    quote = claim.evidence[0].quote
    tokens = title_tokens(quote)
    wanted_measures, wanted_words = measures(tokens), words(tokens)
    if len(wanted_words) < 3:
        return []
    host = _host(str(claim.signal_url))
    found: list[Evidence] = []
    for other in others:
        if _host(str(other.document.url)) == host:
            continue
        for sentence in _SENTENCE.split(other.text):
            have = title_tokens(sentence)
            if wanted_measures <= have and len(wanted_words & have) / len(wanted_words) >= 0.5:
                found.append(Evidence(url=other.document.url, source=other.document.source,
                                      quote=sentence.strip()[:250], primary=other.primary))
                break
    return found


def contradictions(claim: Claim, others: list[Other], overlap: float = 0.5) -> list[tuple[Other, str]]:
    """Phrases d'autres sources sur le même sujet avec des chiffres incompatibles.

    Même sujet : ≥ `overlap` des mots de la citation ; incompatibles : aucun des deux ensembles de
    chiffres n'inclut l'autre (« 0.9 … 1.8x » contre « 0.9 … 3x »)."""
    if claim.kind not in ("chiffre", "benchmark") or not claim.evidence:
        return []
    tokens = title_tokens(claim.evidence[0].quote)
    mine, wanted_words = measures(tokens), words(tokens)
    if not mine or len(wanted_words) < 3:
        return []
    found = []
    for other in others:
        if str(other.document.url) == str(claim.signal_url):
            continue
        for sentence in _SENTENCE.split(other.text):
            have = title_tokens(sentence)
            theirs = measures(have)
            if (theirs and len(wanted_words & have) / len(wanted_words) >= overlap
                    and not theirs <= mine and not mine <= theirs):
                found.append((other, sentence.strip()[:250]))
                break
    return found


def score_claim(claim: Claim, corroborating: list[Evidence], contradicting: int) -> Claim:
    """Statut et confiance 0-100, avec le détail de chaque composante."""
    if claim.status == "non_etaye":
        return claim.model_copy(update={"confidence": 0})
    primary = claim.evidence[0].primary
    reasons = [*claim.reasons, "citation retrouvée dans la source (+40)"]
    score = 40
    if primary:
        score += 25
        reasons.append("source primaire (+25)")
    else:
        score += 5
        reasons.append("source secondaire (+5)")
    if corroborating:
        bonus = min(30, 15 * len(corroborating))
        score += bonus
        reasons.append(f"corroboré par {len(corroborating)} autre(s) source(s) (+{bonus})")
    if claim.missing_protocol:
        score -= 15
        missing = ", ".join(PROTOCOL_LABELS[name] for name in claim.missing_protocol)
        reasons.append(f"protocole de benchmark incomplet : {missing} (−15)")
    if contradicting:
        score -= 30
        reasons.append(f"contredit par {contradicting} source(s) (−30)")
    if claim.kind not in FACT_KINDS:
        score = min(score, 50)
        reasons.append(f"{claim.kind} de la source : plafonné à 50")

    backed_by_primary = primary or any(e.primary for e in corroborating)
    if contradicting:
        status = "conteste"
    elif backed_by_primary and not claim.missing_protocol and claim.kind in FACT_KINDS:
        status = "confirme"
    else:
        status = "rapporte"
    return claim.model_copy(update={
        "status": status,
        "evidence": [*claim.evidence, *corroborating],
        "confidence": max(0, min(100, score)),
        "reasons": reasons,
    })


def signal_confidence(claims: list[Claim], dropped: int = 0) -> tuple[int, list[str]]:
    """Confiance d'un signal : moyenne de ses faits étayés, pénalisée par les affirmations écartées."""
    grounded = [c for c in claims if c.status != "non_etaye"]
    facts = [c for c in grounded if c.kind in FACT_KINDS] or grounded
    if not facts:
        return 0, ["aucune affirmation étayée par une citation"]
    score = sum(c.confidence for c in facts) / len(facts)
    counts: dict[str, int] = {}
    for claim in facts:
        counts[claim.status] = counts.get(claim.status, 0) + 1
    reasons = [f"{len(facts)} fait(s) : " + ", ".join(f"{n} {STATUS_LABELS[s]}" for s, n in counts.items())]
    if dropped:
        score -= 5 * dropped
        reasons.append(f"{dropped} affirmation(s) sans citation écartée(s) (−{5 * dropped})")
    return max(0, min(100, round(score))), reasons


def confidence_label(score: int | None) -> str:
    if score is None:
        return "non évaluée"
    return "élevée" if score >= 75 else "moyenne" if score >= 50 else "faible"


def other_documents(documents: list[Document], text_limit: int, secondary: set[str]) -> list[Other]:
    return [Other(document, document_text(document, text_limit), is_primary(document, secondary))
            for document in documents]
