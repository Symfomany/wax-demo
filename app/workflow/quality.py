"""Qualité de la veille : détection du bruit, dédoublonnage et ranking hybride explicable.

Aucun LLM ici : des règles vérifiables et bon marché (Jetson), pour que le tri ne dépende
pas du modèle. Chaque décision porte sa raison, affichée dans la trace et le digest.

- bruit « dur » (écarté) : mots exclus (profil Grill-me, leçons, sources.toml), commits de
  maintenance (chore, bump, typo), contenu vide ;
- bruit « léger » (pénalisé au classement) : pré-versions (rc, beta, nightly), résumé trop court ;
- doublons : même URL canonique (utm, www, version arXiv) ou titres proches (Jaccard) ;
  un doublon d'un item déjà publié est écarté, sinon le cluster garde son meilleur représentant
  et compte ses sources (corroboration) ;
- ranking hybride : score LLM (pertinence, nouveauté, confiance) + fraîcheur + profil + autorité
  de la source + corroboration − pénalités, avec le détail de chaque composante.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit

from app.harness.hooks import normalize_title
from app.schemas import Document, Signal


# --- Bruit ------------------------------------------------------------------------

DEFAULT_EXCLUSIONS = ("tutorial", "tutoriel", "course", "awesome", "cheatsheet", "cheat sheet")
HARD_NOISE = [
    (re.compile(r"\b(chore|bump(ed)? (version|deps?|dependencies)|typo|dependabot|merge pull request)\b", re.I),
     "maintenance (chore, bump, typo)"),
    (re.compile(r"\b(we'?re hiring|job offer|offre d'emploi|recrute)\b", re.I), "offre d'emploi"),
]
SOFT_NOISE = [
    (re.compile(r"\b(nightly|snapshot|pre-?release|dev\d+)\b", re.I), "pré-version (nightly, pre-release)"),
    (re.compile(r"((?<![a-z])rc\.?\d*\b|\b(alpha|beta)\d*\b)", re.I), "pré-version (rc, alpha, beta)"),
]


def noise_check(document: Document, exclusions: list[str]) -> tuple[str | None, list[str]]:
    """(raison d'exclusion ou None, drapeaux de bruit léger)."""
    # Titre seul (un « of course » dans un résumé n'est pas un cours), sauf pour les dépôts découverts
    # dont la description dit souvent « awesome list », « course material »…
    text = document.title.lower()
    if "github-mcp" in document.tags:
        text += f" {document.summary[:400].lower()}"
    for word in exclusions:
        word = word.strip().lower()
        if word and re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text):
            return f"exclu par le profil ou une leçon : « {word} »", []
    for pattern, reason in HARD_NOISE:
        if pattern.search(document.title):
            return reason, []
    if len(normalize_title(document.title)) < 4 and len(document.summary.strip()) < 20:
        return "contenu vide (titre et résumé inexploitables)", []
    flags = [reason for pattern, reason in SOFT_NOISE if pattern.search(document.title)]
    if len(document.summary.strip()) < 40:
        flags.append("résumé source trop court")
    return None, list(dict.fromkeys(flags))


# --- Doublons -----------------------------------------------------------------------

STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "your", "our", "new", "how", "what", "why", "via", "using",
    "les", "des", "une", "pour", "avec", "dans", "sur", "par", "est", "aux", "nouveau", "nouvelle",
    "release", "released", "version", "announcing", "introducing", "update", "updates",
}
_ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?", re.I)


def canonical_url(url: str) -> str:
    """URL comparable : sans www, schéma, fragment, paramètres de suivi ni version arXiv."""
    if match := _ARXIV.search(url):
        return f"arxiv.org/abs/{match.group(1)}"
    parts = urlsplit(url.strip())
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query)
                             if not k.lower().startswith(("utm_", "ref", "source", "fbclid", "gclid"))))
    path = parts.path.rstrip("/").removesuffix("/index.html")
    return f"{parts.netloc.lower().removeprefix('www.')}{path}" + (f"?{query}" if query else "")


_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")


def title_tokens(title: str) -> frozenset[str]:
    """Mots significatifs du titre ; les jetons chiffrés (v0.6.1, qwen3, 70b) sont toujours gardés."""
    return frozenset(t for t in _TOKEN.findall(title.lower())
                     if any(c.isdigit() for c in t) or (len(t) > 2 and t not in STOPWORDS))


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    # Versions ou numéros différents (v0.6.1 / v0.6.2, GPT-5 / GPT-6) : jamais des doublons.
    if {t for t in a if any(c.isdigit() for c in t)} != {t for t in b if any(c.isdigit() for c in t)}:
        return 0.0
    if len(a) < 3 or len(b) < 3:
        return 1.0 if a and a == b else 0.0
    return len(a & b) / len(a | b)


@dataclass
class Cluster:
    representative: Document
    members: list[Document] = field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        return list(dict.fromkeys(source_key(d) for d in [self.representative, *self.members]))


def source_key(document: Document) -> str:
    return "github-mcp" if "github-mcp" in document.tags else document.source


def completeness(document: Document) -> tuple:
    """Meilleur représentant d'un cluster : source primaire, résumé le plus riche, puis le plus récent."""
    return (SOURCE_AUTHORITY.get(source_key(document), 0.5), len(document.summary),
            document.published_at.timestamp() if document.published_at else 0)


def cluster_documents(documents: list[Document], threshold: float) -> list[Cluster]:
    """Regroupement glouton (O(n²) borné par le pool du prefilter) : URL canonique ou titres proches."""
    clusters: list[tuple[str, frozenset[str], Cluster]] = []
    for document in documents:
        url, tokens = canonical_url(str(document.url)), title_tokens(document.title)
        for known_url, known_tokens, cluster in clusters:
            if url == known_url or similarity(tokens, known_tokens) >= threshold:
                if completeness(document) > completeness(cluster.representative):
                    cluster.members.append(cluster.representative)
                    cluster.representative = document
                else:
                    cluster.members.append(document)
                break
        else:
            clusters.append((url, tokens, Cluster(document)))
    return [cluster for _, _, cluster in clusters]


PublishedIndex = list[tuple[str, frozenset[str], str]]  # (URL canonique, jetons du titre, titre)


def published_index(items: list[tuple[str, str]]) -> PublishedIndex:
    """Index des items déjà publiés [(url, titre)] pour la détection des quasi-doublons."""
    return [(canonical_url(url), title_tokens(title), title) for url, title in items]


def published_duplicate(document: Document, published: PublishedIndex, threshold: float) -> str | None:
    """Titre déjà publié dont ce document est un quasi-doublon (même URL canonique ou titre proche)."""
    url, tokens = canonical_url(str(document.url)), title_tokens(document.title)
    for known_url, known_tokens, title in published:
        if url == known_url or similarity(tokens, known_tokens) >= threshold:
            return title
    return None


# --- Ranking hybride explicable -------------------------------------------------------

# Autorité : les sources primaires (release officielle, blog éditeur) avant un dépôt découvert.
SOURCE_AUTHORITY = {"github": 1.0, "rss": 0.9, "arxiv": 0.8, "github-mcp": 0.6}
SOURCE_LABELS = {"github": "release GitHub officielle", "rss": "blog officiel", "arxiv": "article arXiv",
                 "github-mcp": "dépôt découvert (MCP)"}
DEFAULT_WEIGHTS = {"llm": 0.5, "freshness": 0.15, "profile": 0.15, "source": 0.1, "corroboration": 0.1}
NOISE_PENALTY = 0.08


@dataclass
class RankInput:
    signal: Signal
    document: Document
    sources: int = 1  # sources distinctes du cluster
    flags: list[str] = field(default_factory=list)


def hybrid_rank(items: list[RankInput], now: datetime, max_age_days: int, keywords: list[str],
                weights: dict[str, float] | None = None) -> list[Signal]:
    """Signaux triés par score hybride (0-100), chacun avec sa décomposition et ses raisons."""
    weights = DEFAULT_WEIGHTS | (weights or {})
    wanted = [k.strip().lower() for k in keywords if k and k.strip()]
    ranked = []
    for item in items:
        signal, document = item.signal, item.document
        parts: dict[str, float] = {}
        reasons: list[str] = []

        llm = (2 * signal.relevance + signal.novelty + signal.confidence) / 40
        parts["llm"] = weights["llm"] * llm
        reasons.append(f"LLM : pertinence {signal.relevance}/10, nouveauté {signal.novelty}, "
                       f"confiance {signal.confidence} (+{100 * parts['llm']:.0f})")

        if document.published_at:
            age = max(0.0, (now - document.published_at).total_seconds() / 86400)
            freshness = math.exp(-age / max(1, max_age_days / 2))
            parts["freshness"] = weights["freshness"] * freshness
            reasons.append(f"fraîcheur : publié il y a {age:.0f} j (+{100 * parts['freshness']:.0f})")
        else:
            parts["freshness"] = weights["freshness"] * 0.3
            reasons.append(f"fraîcheur : date inconnue (+{100 * parts['freshness']:.0f})")

        text = f"{document.title} {document.summary} {' '.join(signal.tags)}".lower()
        matched = [k for k in wanted if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", text)]
        parts["profile"] = weights["profile"] * min(1.0, len(matched) / 2)
        if matched:
            reasons.append(f"profil : {', '.join(matched[:4])} (+{100 * parts['profile']:.0f})")

        key = source_key(document)
        parts["source"] = weights["source"] * SOURCE_AUTHORITY.get(key, 0.5)
        reasons.append(f"source : {SOURCE_LABELS.get(key, key)} (+{100 * parts['source']:.0f})")

        parts["corroboration"] = weights["corroboration"] * min(1.0, (item.sources - 1) / 2)
        if item.sources > 1:
            reasons.append(f"corroboré par {item.sources} sources (+{100 * parts['corroboration']:.0f})")

        if item.flags:
            parts["noise"] = -NOISE_PENALTY * len(item.flags)
            reasons.append(f"pénalité : {', '.join(item.flags)} ({100 * parts['noise']:.0f})")

        score = round(max(0.0, 100 * sum(parts.values())), 1)
        ranked.append(signal.model_copy(update={
            "score": score,
            "score_breakdown": {k: round(100 * v, 1) for k, v in parts.items()},
            "rank_reasons": reasons,
        }))
    return sorted(ranked, key=lambda s: s.score or 0, reverse=True)
