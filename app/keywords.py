"""Mots-clés déterministes d'un digest.

1. Tags thématiques des signaux (proposés par le Scout ou issus des topics GitHub).
2. Mots de titre récurrents (présents dans au moins deux signaux).
3. En repli seulement, les mots de titre les plus longs, hors mots-outils.
"""

import re
from collections import Counter

STOPWORDS = {
    # anglais
    "the", "and", "for", "with", "from", "into", "your", "our", "this", "that", "are", "via",
    "new", "how", "what", "why", "using", "use", "towards", "toward", "large", "based", "more",
    "release", "releases", "introducing", "open", "source", "model", "models", "between",
    "before", "after", "about", "across", "over", "under", "when", "where", "which", "while",
    "land", "lands", "gap", "missing", "middle", "exposes",
    # français
    "les", "des", "une", "pour", "avec", "dans", "sur", "par", "plus", "nouveau", "nouvelle",
    "dépôt", "depot", "veille",
}
SOURCE_TAGS = {"rss", "arxiv", "github", "github-mcp", "prerelease"}


def _tag_terms(tags: list[str]) -> list[tuple[str, int]]:
    """(terme, poids) : les tags thématiques du Scout (position ≥ 2) pèsent double,
    les tags de collecte (langage d'un dépôt…) comptent simple."""
    terms = []
    for position, tag in enumerate(tags):
        tag = tag.strip().lower()
        # noms de flux (« hugging face blog »), dépôts « owner/repo », catégories arXiv
        if not tag or tag in SOURCE_TAGS or "/" in tag or " " in tag or re.fullmatch(r"cs\.\w+", tag):
            continue
        terms.append((tag, 2 if position >= 2 else 1))
    return terms


def _title_words(title: str) -> set[str]:
    title = re.sub(r"\([^)]*\)", " ", title)  # « (420★) »
    title = re.sub(r"[\w.-]+/([\w.-]+)", r"\1", title)  # « owner/repo » → « repo »
    return {
        word for word in re.findall(r"[a-zA-ZÀ-ÿ][\w\-]{2,}", title.lower())
        if word not in STOPWORDS and not word.isdigit()
    }


def extract_keywords(digest: dict, limit: int = 8, minimum: int = 3) -> list[str]:
    tags: Counter[str] = Counter()
    titles: Counter[str] = Counter()
    for item in digest.get("items", []):
        for term, weight in _tag_terms(item.get("tags", [])):
            tags[term] += weight
        titles.update(_title_words(item.get("title", "")))  # une fois par signal

    scores = tags + Counter({word: count for word, count in titles.items() if count >= 2})
    keywords = [term for term, _ in scores.most_common(limit)]
    if len(keywords) < minimum:
        fallback = sorted((w for w in titles if w not in keywords), key=lambda w: (-len(w), w))
        keywords += fallback[: minimum - len(keywords)]
    return keywords
