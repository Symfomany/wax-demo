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
        f"{item.summary} {item.why_it_matters}" for item in digest.items
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


PUBLISH_GUARDS: list[PublishGuard] = [
    guard_not_empty,
    guard_grounding,
    guard_urls_in_prose,
    guard_dates,
    guard_secrets,
]


def run_publish_guards(digest: Digest, known_urls: set[str], now: datetime) -> list[str]:
    return [violation for guard in PUBLISH_GUARDS for violation in guard(digest, known_urls, now)]
