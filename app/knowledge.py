"""Base de connaissances Markdown : glossaire, index de mots-clés, règles métiers, prompts.

- `knowledge/` (Git) : fichiers livrés avec le dépôt, jamais modifiés par l'interface ;
- `data/knowledge/` : fichiers téléversés, validés (Pydantic) avant écriture, supprimables.

Un fichier = front matter facultatif (`title`, `type`) + entrées `## Titre`. En tête
d'entrée, des lignes `Clé : valeur` (Domaine, Alias, Mots-clés, Source, Cible) ; le reste
est le corps Markdown. Pour `type: regles`, chaque puce du corps est une règle.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError

from app.config import settings

Kind = Literal["glossaire", "regles", "prompts", "note"]
KINDS = ("glossaire", "regles", "prompts", "note")
MAX_UPLOAD_BYTES = 200_000
UPLOAD_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\.md$")
IGNORED_FILES = {"readme.md"}

# Clés reconnues en tête d'entrée (comparées sans accents ni casse).
META_KEYS = {
    "domaine": "domain", "domain": "domain",
    "alias": "aliases", "aliases": "aliases",
    "mots-cles": "keywords", "mots cles": "keywords", "keywords": "keywords",
    "source": "sources", "sources": "sources",
    "cible": "target", "target": "target",
}
META_LINE = re.compile(r"^\s*([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ \-]{1,20}?)\s*:\s*(.+?)\s*$")
BULLET = re.compile(r"^\s*[-*]\s+(.+)$")

STOPWORDS = {
    "le", "la", "les", "un", "une", "des", "du", "de", "d", "l", "et", "ou", "en", "au", "aux",
    "que", "qu", "qui", "quoi", "est", "ce", "c", "ca", "sur", "pour", "par", "dans", "avec",
    "moi", "mes", "ma", "mon", "quel", "quelle", "quels", "quelles", "donne", "explique",
    "definition", "definir", "definis", "signifie", "veut", "dire", "glossaire", "terme",
    "the", "a", "an", "of", "what", "is", "are", "and", "or", "to", "in", "for", "on",
    "regle", "regles", "metier", "metiers", "domaine", "base", "connaissance", "connaissances",
    "knowledge", "dis", "parle", "s", "il", "y", "a-t-il", "sont",
}


class KnowledgeError(ValueError):
    pass


class KnowledgeExists(KnowledgeError):
    pass


class KnowledgeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    file: str
    origin: Literal["base", "upload"]
    kind: Kind
    title: str = Field(min_length=1, max_length=200)
    domain: str = Field("", max_length=80)
    aliases: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    sources: list[HttpUrl] = Field(default_factory=list)
    target: Literal["chat", "review"] | None = None
    body: str = ""
    rules: list[str] = Field(default_factory=list)

    @property
    def url(self) -> str:
        """Lien d'une entrée : sa source primaire, sinon son ancre dans l'onglet Knowledge."""
        return str(self.sources[0]) if self.sources else f"#k={self.id}"


class KnowledgeFile(BaseModel):
    name: str
    origin: Literal["base", "upload"]
    title: str
    kind: Kind
    entries: int
    size: int


def fold(text: str) -> str:
    """Minuscules sans accents : comparaison tolérante (« Quantification » = « quantification »)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")[:60] or "entree"


def _split_list(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;]", value) if item.strip()]


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.DOTALL)
    if not match:
        raise KnowledgeError("Front matter non fermé (ligne --- manquante).")
    meta = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        if key.strip():
            meta[key.strip().lower()] = value.strip().strip("\"'")
    return meta, text[match.end():]


def _sections(body: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """(préambule, [(titre ## , lignes)]) en ignorant les titres dans les blocs de code."""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    fenced = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            preamble.append(line)
    return "\n".join(preamble), sections


def _entry_fields(lines: list[str]) -> tuple[dict, str]:
    meta: dict = {}
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    while index < len(lines):
        match = META_LINE.match(lines[index])
        key = META_KEYS.get(fold(match.group(1)).strip()) if match else None
        if not key:
            break
        value = match.group(2)
        if key in {"aliases", "keywords", "sources"}:
            meta.setdefault(key, []).extend(_split_list(value))
        else:
            meta[key] = value.strip().lower() if key == "target" else value.strip()
        index += 1
    return meta, "\n".join(lines[index:]).strip()


def parse_markdown(text: str, name: str, origin: str = "upload") -> tuple[KnowledgeFile, list[KnowledgeEntry]]:
    """Analyse et valide un fichier ; lève KnowledgeError avec un message lisible."""
    meta, body = _frontmatter(text.replace("\r\n", "\n"))
    kind = meta.get("type", "note").lower()
    if kind not in KINDS:
        raise KnowledgeError(f"type « {kind} » inconnu (attendu : {', '.join(KINDS)})")
    preamble, sections = _sections(body)
    heading = re.search(r"^#\s+(.+)$", preamble, re.MULTILINE)
    stem = Path(name).stem
    title = meta.get("title") or (heading.group(1).strip() if heading else stem)
    if not sections:  # note libre : une seule entrée, tout le fichier
        sections = [(title, re.sub(r"^#\s+.+$", "", preamble, count=1, flags=re.MULTILINE).splitlines())]

    entries: list[KnowledgeEntry] = []
    seen: set[str] = set()
    for heading_text, lines in sections:
        fields, entry_body = _entry_fields(lines)
        slug = base = slugify(heading_text)
        counter = 2
        while slug in seen:
            slug, counter = f"{base}-{counter}", counter + 1
        seen.add(slug)
        rules = [m.group(1).strip() for line in entry_body.splitlines() if (m := BULLET.match(line))] \
            if kind == "regles" else []
        try:
            entries.append(KnowledgeEntry(
                id=f"{stem}/{slug}", file=name, origin=origin, kind=kind, title=heading_text,
                body=entry_body, rules=rules, **fields,
            ))
        except ValidationError as error:
            problems = "; ".join(f"{'.'.join(map(str, e['loc']))} : {e['msg']}" for e in error.errors())
            raise KnowledgeError(f"Entrée « {heading_text} » invalide — {problems}") from error
    if not any(e.body or e.rules for e in entries):
        raise KnowledgeError("Aucun contenu : ajoutez des entrées « ## Titre » ou du texte.")
    return KnowledgeFile(name=name, origin=origin, title=title, kind=kind, entries=len(entries),
                         size=len(text.encode("utf-8"))), entries


# --- Base chargée ---------------------------------------------------------------------


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9.+\-]*", fold(text)) if w not in STOPWORDS]


def _contains(haystack: str, needle: str) -> bool:
    """`needle` présent comme expression entière dans `haystack` (tous deux « fold »)."""
    return bool(needle) and re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


@dataclass
class KnowledgeBase:
    files: list[KnowledgeFile] = field(default_factory=list)
    entries: list[KnowledgeEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def get(self, entry_id: str) -> KnowledgeEntry | None:
        return next((e for e in self.entries if e.id == entry_id), None)

    def of_kind(self, kind: str) -> list[KnowledgeEntry]:
        return [e for e in self.entries if e.kind == kind]

    def search(self, query: str, limit: int = 6, kinds: set[str] | None = None) -> list[KnowledgeEntry]:
        """Classement simple : terme ou alias cité tel quel ≫ mots du titre ≫ mots-clés ≫ corps."""
        phrase, wanted = fold(query), set(tokens(query))
        scored = []
        for entry in self.entries:
            if kinds and entry.kind not in kinds:
                continue
            names = [fold(entry.title), *map(fold, entry.aliases)]
            score = sum(12 for name in names if len(name) > 1 and _contains(phrase, name))
            title_words, extra = set(tokens(entry.title)), fold(" ".join([*entry.aliases, *entry.keywords]))
            body, domain = fold(entry.body), fold(entry.domain)
            for token in wanted:
                score += 5 * (token in title_words) + 3 * _contains(extra, token) \
                    + 2 * (token == domain) + 1 * _contains(body, token)
            if score:
                scored.append((score, entry.kind == "glossaire", entry))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [entry for *_, entry in scored[:limit]]

    def keyword_index(self) -> list[dict]:
        """Index A→Z : chaque terme, alias et mot-clé renvoie aux entrées qui le portent."""
        index: dict[str, dict] = {}
        for entry in self.entries:
            if entry.kind == "prompts":
                continue
            for term, role in [(entry.title, "terme"), *((a, "alias") for a in entry.aliases),
                               *((k, "mot-clé") for k in entry.keywords)]:
                key = fold(term)
                item = index.setdefault(key, {"term": term, "entries": []})
                if role == "terme":
                    item["term"] = term
                if all(ref["id"] != entry.id for ref in item["entries"]):
                    item["entries"].append({"id": entry.id, "title": entry.title, "kind": entry.kind, "role": role})
        return sorted(index.values(), key=lambda item: fold(item["term"]))

    def domains(self) -> list[str]:
        return sorted({e.domain for e in self.entries if e.domain}, key=fold)

    def detect_domains(self, text: str, limit: int = 2) -> list[str]:
        """Domaines d'un texte d'après les mots-clés des règles métiers (déterministe)."""
        folded = fold(text)
        scores = []
        for entry in self.of_kind("regles"):
            if fold(entry.domain or entry.title) == "transverse":
                continue
            hits = sum(len(re.findall(rf"(?<![a-z0-9]){re.escape(fold(k))}(?![a-z0-9])", folded))
                       for k in entry.keywords if len(k) > 1)
            if hits >= 2:
                scores.append((hits, entry.domain or entry.title))
        return [domain for _, domain in sorted(scores, reverse=True)[:limit]]

    def rules_for(self, domains: list[str]) -> list[tuple[str, str]]:
        """(domaine, règle) : règles transverses puis celles des domaines demandés."""
        wanted = {"transverse", *map(fold, domains)}
        return [(entry.domain or entry.title, rule) for entry in self.of_kind("regles")
                if fold(entry.domain or entry.title) in wanted for rule in entry.rules]

    def glossary_terms_in(self, text: str, limit: int = 12) -> list[KnowledgeEntry]:
        """Entrées du glossaire dont le terme (ou un alias) apparaît dans le texte."""
        folded = fold(text)
        found = []
        for entry in self.of_kind("glossaire"):
            names = [entry.title, *entry.aliases]
            # Sigles courts (RAG, MCP…) : casse exacte, pour éviter les faux positifs.
            if any((re.search(rf"(?<![A-Za-z0-9]){re.escape(n)}(?![A-Za-z0-9])", text) if len(n) <= 4
                    else _contains(folded, fold(n))) for n in names if len(n) > 1):
                found.append(entry)
        return found[:limit]


_CACHE: dict[tuple, KnowledgeBase] = {}


def _markdown_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.md") if p.name.lower() not in IGNORED_FILES)


def load_knowledge(base_dir: Path | None = None, uploads_dir: Path | None = None) -> KnowledgeBase:
    """Charge (avec cache invalidé par date de modification) les fichiers du dépôt puis les téléversés."""
    base_dir = base_dir or settings.knowledge_dir
    uploads_dir = uploads_dir or settings.knowledge_uploads_dir
    files = [(p, "base") for p in _markdown_files(base_dir)] + [(p, "upload") for p in _markdown_files(uploads_dir)]
    key = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p, _ in files)
    if key in _CACHE:
        return _CACHE[key]
    base = KnowledgeBase()
    for path, origin in files:
        try:
            info, entries = parse_markdown(path.read_text(encoding="utf-8"), path.name, origin)
        except (KnowledgeError, UnicodeDecodeError) as error:
            base.errors.append(f"{path.name} : {error}")
            continue
        base.files.append(info)
        base.entries.extend(entries)
    _CACHE.clear()
    _CACHE[key] = base
    return base


# --- Fichiers téléversés ------------------------------------------------------------------


def upload_name(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    if not name.lower().endswith(".md"):
        raise KnowledgeError("Seuls les fichiers Markdown (.md) sont acceptés.")
    name = f"{slugify(name[:-3])[:60]}.md"
    if not UPLOAD_NAME.match(name):
        raise KnowledgeError(f"Nom de fichier invalide : {filename}")
    return name


def save_upload(filename: str, content: str, replace: bool = False) -> KnowledgeFile:
    name = upload_name(filename)
    if len(content.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise KnowledgeError(f"Fichier trop volumineux ({MAX_UPLOAD_BYTES // 1000} ko maximum).")
    if name in {p.name for p in _markdown_files(settings.knowledge_dir)}:
        raise KnowledgeError(f"« {name} » est un fichier du dépôt : choisissez un autre nom.")
    info, _ = parse_markdown(content, name, "upload")  # validation avant toute écriture
    target = settings.knowledge_uploads_dir / name
    if target.exists() and not replace:
        raise KnowledgeExists(f"« {name} » existe déjà : confirmez le remplacement.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".md.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(target)
    return info


def delete_upload(name: str) -> None:
    if not UPLOAD_NAME.match(name):
        raise KnowledgeError(f"Nom de fichier invalide : {name}")
    target = settings.knowledge_uploads_dir / name
    if not target.exists():
        raise KnowledgeError(f"Fichier téléversé introuvable : {name} (les fichiers du dépôt ne se suppriment pas ici)")
    target.unlink()


def read_file(name: str) -> dict:
    if not UPLOAD_NAME.match(name):
        raise KnowledgeError(f"Nom de fichier invalide : {name}")
    for directory, origin in [(settings.knowledge_uploads_dir, "upload"), (settings.knowledge_dir, "base")]:
        path = directory / name
        if path.is_file():
            return {"name": name, "origin": origin, "markdown": path.read_text(encoding="utf-8")}
    raise KnowledgeError(f"Fichier introuvable : {name}")
