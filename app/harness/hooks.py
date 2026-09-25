"""Hooks déterministes du harness.

Ils encadrent les agents LLM : contrôles avant évaluation (critic) et
garde-fous bloquants avant publication. Aucun LLM ici : uniquement des règles
vérifiables, pour que la conformité ne dépende pas du modèle.
"""

import re
from collections.abc import Callable
from datetime import datetime, timedelta

from app.schemas import Digest, Document


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),  # OpenAI / Langfuse secret
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),  # GitHub
    re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"hf_[A-Za-z0-9]{30,}"),  # Hugging Face
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
URL_PATTERN = re.compile(r"https?://[^\s)\]>\"']+")


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def find_secrets(text: str) -> list[str]:
    return [pattern.pattern for pattern in SECRET_PATTERNS if pattern.search(text)]


# --- Contrôles pré-critic ---------------------------------------------------


def document_risks(document: Document, now: datetime, max_age_days: int) -> list[str]:
    """Risques factuels détectables sans LLM pour un document source."""
    risks = []

    if document.published_at is None:
        risks.append("date de publication absente")
    elif document.published_at > now + timedelta(days=1):
        risks.append("date de publication dans le futur")
    elif document.published_at < now - timedelta(days=max_age_days):
        risks.append(f"document de plus de {max_age_days} jours")

    if len(document.summary.strip()) < 40:
        risks.append("résumé source trop court pour vérifier les affirmations")

    return risks


# --- Garde-fous de publication ---------------------------------------------

PublishGuard = Callable[[Digest, set[str], datetime], list[str]]


def guard_grounding(digest: Digest, known_urls: set[str], now: datetime) -> list[str]:
    return [
        f"URL non issue de la collecte : {item.url}"
        for item in digest.items
        if str(item.url) not in known_urls
    ]


def guard_urls_in_prose(digest: Digest, known_urls: set[str], now: datetime) -> list[str]:
    texts = [digest.executive_summary] + [
        f"{item.summary} {item.why_it_matters} {item.analysis} {item.hypothesis}" for item in digest.items
    ]
    return [
        f"URL inventée dans le texte : {url}"
        for text in texts
        for url in URL_PATTERN.findall(text)
        if url.rstrip(".,;") not in known_urls
    ]


def guard_dates(digest: Digest, known_urls: set[str], now: datetime) -> list[str]:
    return [
        f"Date future pour « {item.title} » : {item.date}"
        for item in digest.items
        if item.date and item.date > now + timedelta(days=1)
    ]


def guard_secrets(digest: Digest, known_urls: set[str], now: datetime) -> list[str]:
    return [f"Motif de secret détecté : {pattern}" for pattern in find_secrets(digest.model_dump_json())]


def guard_not_empty(digest: Digest, known_urls: set[str], now: datetime) -> list[str]:
    return [] if digest.items else ["Digest vide : aucun signal validé."]


def guard_evidence(digest: Digest, known_urls: set[str], now: datetime) -> list[str]:
    """Un fait publié a une citation ; « confirmé » exige une source primaire ; jamais de « non étayé »."""
    violations = []
    for item in digest.items:
        for fact in item.facts:
            if fact.status == "non_etaye" or not fact.evidence or not fact.evidence[0].quote.strip():
                violations.append(f"Fait sans preuve publié pour « {item.title} » : {fact.text[:80]}")
            elif fact.status == "confirme" and not any(e.primary for e in fact.evidence):
                violations.append(f"Fait « confirmé » sur source secondaire seule : {fact.text[:80]}")
    return violations


PUBLISH_GUARDS: list[PublishGuard] = [
    guard_not_empty,
    guard_grounding,
    guard_urls_in_prose,
    guard_dates,
    guard_secrets,
    guard_evidence,
]


def run_publish_guards(digest: Digest, known_urls: set[str], now: datetime) -> list[str]:
    return [violation for guard in PUBLISH_GUARDS for violation in guard(digest, known_urls, now)]


# --- Chiffres non sourcés (paraphrase au-delà de l'extrait) -----------------------------

# Nombres « significatifs » : décimaux (0.9, 1,8), pourcentages et facteurs (40 %, 2x), ≥ 3 chiffres
# (2026, 4096). Les petits entiers seuls (« 3 modèles ») ne sont pas contrôlés.
NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)+|\d+(?=\s?(?:%|x\b|×))|\d{3,}")
_ANY_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _number(value: str) -> str:
    return value.replace(",", ".")


def unsourced_numbers(text: str, source: str) -> list[str]:
    """Nombres significatifs du texte absents de la source (virgule décimale = point)."""
    known = {_number(n) for n in _ANY_NUMBER.findall(source)}
    known |= {part for n in known for part in n.split(".")}  # « v0.9.1 » couvre « 0.9 »… et ses parties
    known |= {".".join(n.split(".")[:k]) for n in known for k in range(2, n.count(".") + 1)}
    return list(dict.fromkeys(n for n in (_number(m) for m in NUMBER_PATTERN.findall(text)) if n not in known))


def guard_numbers(digest: Digest, sources: dict[str, str]) -> list[str]:
    """Chaque chiffre de la prose de l'Editor doit figurer dans la source de l'item (le résumé
    exécutif : dans l'une des sources). Protège contre la paraphrase d'un petit modèle local."""
    violations = []
    for item in digest.items:
        prose = f"{item.summary} {item.why_it_matters} {item.analysis} {item.hypothesis}"
        for number in unsourced_numbers(prose, sources.get(str(item.url), "")):
            violations.append(f"Chiffre absent de la source pour « {item.title[:60]} » : {number}")
    for number in unsourced_numbers(digest.executive_summary, "\n".join(sources.values())):
        violations.append(f"Chiffre absent des sources dans la synthèse : {number}")
    return violations
