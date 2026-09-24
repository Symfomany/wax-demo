"""Review d'une actualité à partir de son URL : scraping → synthèse sourcée → challenge.

Agent **Reviewer** (graphe LangGraph) : fetch → analyze → guard → save.

- fetch   : téléchargement borné (http/https, adresses publiques uniquement, chaque
            redirection revérifiée), extraction du texte principal. Titre, site et date
            sont recopiés depuis les métadonnées de la page, jamais produits par le LLM ;
- analyze : domaines détectés par les mots-clés des règles métiers (déterministe), puis
            LLM en sortie structurée guidé par le skill `review-actu`, les critères du skill
            `veille-tech`, les règles métiers, le glossaire et la mémoire de veille ;
- guard   : une affirmation dont la citation est introuvable dans la page devient « non
            étayée » ; règles citées par numéro seulement ; URL étrangères à l'article retirées ;
- save    : validation Pydantic du record complet, puis SQLite (table `reviews`, v5).

Le challenge se fait ensuite dans le chat (outil `challenge_review`) ; une révision relance
le graphe sans refetch, avec les objections du débat.
"""

from __future__ import annotations

import ipaddress
from html import escape
import json
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal, TypedDict
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
from dateutil import parser as date_parser
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app import storage
from app.config import settings
from app.harness.guards import sanitize_untrusted
from app.harness.hooks import URL_PATTERN
from app.harness.prompts import render_prompt
from app.harness.skills import load_skill
from app.knowledge import KnowledgeBase, load_knowledge, tokens
from app.llm import StructuredLLM


class FetchError(RuntimeError):
    pass


# --- Extraction HTML → texte ----------------------------------------------------------

SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form",
             "iframe", "template", "button", "select", "canvas"}
BLOCK_TAGS = {"p", "div", "section", "article", "main", "li", "ul", "ol", "br", "tr", "table",
              "pre", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt", "figcaption", "hr"}
DATE_META = {"article:published_time", "og:published_time", "datepublished", "date", "pubdate",
             "publish-date", "dc.date", "dc.date.issued", "citation_publication_date", "citation_date",
             "parsely-pub-date", "sailthru.date"}


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self.blocks: list[tuple[str, int, int, str]] = []  # (texte, dans <article>, dans <main>, balise)
        self.jsonld: list[str] = []
        self._skip = 0
        self._article = 0
        self._main = 0
        self._in_title = False
        self._in_jsonld = False
        self._buffer: list[str] = []
        self._block_tag = "p"
        self.times: list[str] = []

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
        if text:
            self.blocks.append((text, self._article, self._main, self._block_tag))
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        attributes = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or attributes.get("itemprop") or "").lower()
            if key and attributes.get("content"):
                self.meta.setdefault(key, attributes["content"].strip())
            return
        if tag == "link" and "canonical" in attributes.get("rel", "").lower():
            self.meta.setdefault("canonical", attributes.get("href", ""))
            return
        if tag == "script" and "ld+json" in attributes.get("type", ""):
            self._in_jsonld = True
        if tag == "time" and attributes.get("datetime"):
            self.times.append(attributes["datetime"])
        if tag == "title":
            self._in_title = True
        if tag in SKIP_TAGS:
            self._skip += 1
        if tag == "article":
            self._article += 1
        if tag == "main":
            self._main += 1
        if tag in BLOCK_TAGS:
            self._flush()
            self._block_tag = tag

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_jsonld = False
        if tag == "title":
            self._in_title = False
        if tag in BLOCK_TAGS:
            self._flush()
            self._block_tag = "p"
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "article" and self._article:
            self._article -= 1
        if tag == "main" and self._main:
            self._main -= 1

    def handle_data(self, data):
        if self._in_jsonld:
            self.jsonld.append(data)
        elif self._in_title:
            self.title += data
        elif not self._skip:
            self._buffer.append(data)


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date_parser.parse(value, fuzzy=False).date().isoformat()
    except (ValueError, OverflowError, TypeError):
        return None


def _jsonld_date(chunks: list[str]) -> str | None:
    for chunk in chunks:
        match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', chunk)
        if match:
            return match.group(1)
    return None


class Page(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    final_url: HttpUrl
    title: str = Field(min_length=1, max_length=500)
    site: str = ""
    description: str = ""
    published_at: str | None = None  # AAAA-MM-JJ, uniquement depuis les métadonnées de la page
    text: str
    word_count: int
    truncated: bool = False


def extract_page(html: str, url: str, final_url: str | None = None, max_chars: int | None = None) -> Page:
    """Texte principal d'une page HTML : <article>, sinon <main>, sinon tout le corps."""
    parser = _PageParser()
    parser.feed(html)
    parser.close()
    parser._flush()
    blocks = parser.blocks

    def words(selection):
        return sum(len(text.split()) for text, *_ in selection)

    article = [b for b in blocks if b[1]]
    main = [b for b in blocks if b[2]]
    chosen = article if words(article) >= 120 else main if words(main) >= 120 else blocks
    paragraphs: list[str] = []
    for text, _, _, tag in chosen:
        if tag in {"h1", "h2", "h3", "h4"}:
            text = "## " + text
        elif len(text.split()) < 4 and tag not in {"li", "pre"}:
            continue  # menus, boutons, légendes isolées
        if not paragraphs or paragraphs[-1] != text:
            paragraphs.append(text)
    text = "\n\n".join(paragraphs)
    limit = max_chars or settings.review_max_chars
    meta = parser.meta
    title = (meta.get("og:title") or parser.title or meta.get("twitter:title") or "").strip()
    raw_date = next((meta[k] for k in DATE_META if meta.get(k)), None) or _jsonld_date(parser.jsonld) \
        or (parser.times[0] if parser.times else None)
    final = final_url or url
    return Page(
        url=url,
        final_url=final,
        title=re.sub(r"\s+", " ", title)[:500] or urlsplit(final).netloc,
        site=(meta.get("og:site_name") or urlsplit(final).netloc.removeprefix("www.")).strip(),
        description=(meta.get("og:description") or meta.get("description") or "").strip()[:600],
        published_at=_iso_date(raw_date),
        text=text[:limit],
        word_count=len(text.split()),
        truncated=len(text) > limit,
    )


# --- Téléchargement borné --------------------------------------------------------------

ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
USER_AGENT = "Mozilla/5.0 (compatible; LLMWatchHarness/0.5; review)"


def system_resolve(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except socket.gaierror as error:
        raise FetchError(f"Nom de domaine introuvable : {host}") from error


def check_url(url: str, resolve: Callable[[str], list[str]] = system_resolve,
              allow_private: bool | None = None) -> str:
    """Refuse ce qui n'est pas http(s) public : pas de fichier local, ni de réseau interne (SSRF)."""
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise FetchError("URL invalide : seules les adresses http(s) sont acceptées.")
    if parts.username or parts.password:
        raise FetchError("URL avec identifiants refusée.")
    allow = settings.review_allow_private if allow_private is None else allow_private
    if not allow:
        for address in resolve(parts.hostname):
            ip = ipaddress.ip_address(address.split("%")[0])
            if not ip.is_global:
                raise FetchError(f"Adresse non publique refusée : {parts.hostname} ({ip})")
    return url.strip()


def download(url: str, client: httpx.Client | None = None,
             resolve: Callable[[str], list[str]] = system_resolve,
             allowed_types: tuple[str, ...] | None = ALLOWED_TYPES) -> tuple[str, str, str]:
    """(URL finale, type de contenu, texte) d'une ressource http(s) publique.

    Chaque redirection est revérifiée (SSRF) ; la taille est bornée. `allowed_types=None`
    accepte tout type (flux RSS/Atom servis en application/xml, etc.).
    """
    own_client = client is None
    client = client or httpx.Client(timeout=settings.review_timeout, headers={"User-Agent": USER_AGENT})
    try:
        current = url
        for _ in range(6):
            check_url(current, resolve)
            with client.stream("GET", current, follow_redirects=False) as response:
                if response.is_redirect:
                    current = urljoin(current, response.headers.get("location", ""))
                    continue
                if response.status_code >= 400:
                    raise FetchError(f"La page a répondu HTTP {response.status_code}.")
                content_type = response.headers.get("content-type", "text/html").split(";")[0].strip().lower()
                if allowed_types is not None and content_type not in allowed_types:
                    raise FetchError(f"Type de contenu non pris en charge : {content_type} (HTML ou texte attendu).")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) >= settings.review_max_bytes:
                        break
                encoding = response.charset_encoding or "utf-8"
                return current, content_type, bytes(body[: settings.review_max_bytes]).decode(encoding, errors="replace")
        raise FetchError("Trop de redirections.")
    except httpx.HTTPError as error:
        raise FetchError(f"Téléchargement impossible : {type(error).__name__} {error}") from error
    finally:
        if own_client:
            client.close()


def fetch_page(url: str, client: httpx.Client | None = None,
               resolve: Callable[[str], list[str]] = system_resolve) -> Page:
    final_url, content_type, raw = download(url, client, resolve)
    if content_type == "text/plain":
        raw = "".join(f"<p>{escape(p)}</p>" for p in raw.split("\n\n") if p.strip())
    page = extract_page(raw, url, final_url)
    if page.word_count < 30:
        raise FetchError("Texte extrait trop court : page vide, protégée ou rendue en JavaScript.")
    return page


# --- Sortie LLM et record validé -------------------------------------------------------

ClaimKind = Literal["fait", "chiffre", "benchmark", "annonce", "opinion"]


class ReviewClaim(BaseModel):
    claim: str = Field(description="Affirmation de l'article, reformulée en une phrase")
    quote: str = Field("", description="Passage copié mot pour mot de l'article (300 caractères max), vide si aucun")
    kind: ClaimKind


class RuleCheck(BaseModel):
    rule_id: int = Field(description="Numéro [Rn] de la règle métier")
    verdict: Literal["ok", "ko", "na"]
    note: str = ""


class ReviewDraft(BaseModel):
    """Sortie du LLM (validée par Pydantic) : pas d'URL, pas de titre, pas de date."""

    summary: str = Field(description="3 à 5 phrases factuelles")
    key_points: list[str] = Field(default_factory=list, max_length=8)
    claims: list[ReviewClaim] = Field(min_length=1, max_length=10)
    why_it_matters: str
    source_type: Literal["primaire", "secondaire", "inconnu"]
    source_type_reason: str = ""
    relevance: int = Field(ge=0, le=10)
    novelty: int = Field(ge=0, le=10)
    confidence: int = Field(ge=0, le=10)
    risks: list[str] = Field(default_factory=list, max_length=8)
    rule_checks: list[RuleCheck] = Field(min_length=1, max_length=20)
    questions: list[str] = Field(default_factory=list, max_length=8)
    tags: list[str] = Field(default_factory=list, max_length=10)


class CheckedClaim(ReviewClaim):
    status: Literal["etaye", "non_etaye"]


class CheckedRule(BaseModel):
    rule_id: int
    domain: str
    rule: str
    verdict: Literal["ok", "ko", "na"]
    note: str = ""


class ReviewAnalysis(ReviewDraft):
    claims: list[CheckedClaim] = Field(default_factory=list)
    rule_checks: list[CheckedRule] = Field(default_factory=list)


class GlossaryRef(BaseModel):
    id: str
    title: str


class ReviewPage(Page):
    domains: list[str] = Field(default_factory=list)
    glossary: list[GlossaryRef] = Field(default_factory=list)


class ReviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    url: HttpUrl
    title: str
    conversation_id: str
    revision: int = Field(ge=1)
    model: str
    page: ReviewPage
    analysis: ReviewAnalysis
    warnings: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)  # règles soumises au Reviewer, numérotées
    objections: str = ""


# --- Guard ---------------------------------------------------------------------------------


def _normalize(text: str) -> str:
    text = text.lower().translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "«": '"', "»": '"',
                                                 "–": "-", "—": "-", " ": " "}))
    return re.sub(r"\s+", " ", text).strip(" .\"'")


def quote_in_text(quote: str, text: str) -> bool:
    """Chaque fragment d'une citation (séparés par « … ») doit figurer dans l'article."""
    fragments = [_normalize(f) for f in re.split(r"\.\.\.|…|\[\.\.\.\]", quote)]
    fragments = [f for f in fragments if len(f) >= 8]
    haystack = _normalize(text)
    return bool(fragments) and all(fragment in haystack for fragment in fragments)


def guard_review(draft: ReviewDraft, page: Page, rules: list[tuple[str, str]]) -> tuple[ReviewAnalysis, list[str]]:
    warnings: list[str] = []
    allowed = {str(page.url), str(page.final_url)}

    def clean(text: str) -> str:
        def strip(match: re.Match) -> str:
            url = match.group(0).rstrip(".,;)")
            if url in allowed:
                return match.group(0)
            warnings.append(f"URL non sourcée retirée : {url}")
            return "[lien retiré]"

        return URL_PATTERN.sub(strip, text).strip()

    claims = []
    for claim in draft.claims:
        found = quote_in_text(claim.quote, page.text) if claim.quote.strip() else False
        if claim.quote.strip() and not found:
            warnings.append(f"Citation introuvable dans la page, affirmation marquée non étayée : « {claim.claim[:80]} »")
        claims.append(CheckedClaim(claim=clean(claim.claim), quote=claim.quote.strip()[:400] if found else "",
                                   kind=claim.kind, status="etaye" if found else "non_etaye"))
    checks, seen = [], set()
    for check in draft.rule_checks:
        if not 1 <= check.rule_id <= len(rules):
            warnings.append(f"Règle R{check.rule_id} inexistante ignorée")
            continue
        if check.rule_id in seen:
            continue
        seen.add(check.rule_id)
        domain, rule = rules[check.rule_id - 1]
        checks.append(CheckedRule(rule_id=check.rule_id, domain=domain, rule=rule,
                                  verdict=check.verdict, note=clean(check.note)))
    checks.sort(key=lambda c: c.rule_id)
    unsupported = sum(c.status == "non_etaye" for c in claims)
    analysis = ReviewAnalysis(
        summary=clean(draft.summary),
        key_points=[clean(p) for p in draft.key_points if p.strip()],
        claims=claims,
        why_it_matters=clean(draft.why_it_matters),
        source_type=draft.source_type,
        source_type_reason=clean(draft.source_type_reason),
        relevance=draft.relevance,
        novelty=draft.novelty,
        # Confiance plafonnée quand la moitié des affirmations n'est pas étayée par la page.
        confidence=min(draft.confidence, 5) if claims and unsupported * 2 >= len(claims) else draft.confidence,
        risks=[clean(r) for r in draft.risks if r.strip()],
        rule_checks=checks,
        questions=[clean(q) for q in draft.questions if q.strip()],
        tags=[t.strip().lower() for t in draft.tags if t.strip()][:10],
    )
    return analysis, warnings


# --- Graphe Reviewer -----------------------------------------------------------------------


class ReviewState(TypedDict, total=False):
    url: str
    review_id: str
    objections: str
    previous: dict
    page: dict
    domains: list[str]
    glossary: list[dict]
    rules: list[list[str]]
    draft: dict
    analysis: dict
    warnings: list[str]
    record: dict


@dataclass
class ReviewContext:
    llm: StructuredLLM
    connection: object
    fetch: Callable[[str], Page] = fetch_page
    knowledge: Callable[[], KnowledgeBase] = load_knowledge
    memory: Callable[[], str] = lambda: "Aucun."
    skill_path: Path = field(default_factory=lambda: settings.review_skill_path)
    criteria_skill_path: Path = field(default_factory=lambda: settings.skill_path)


def _skill_section(path: Path, title: str) -> str:
    try:
        return load_skill(path).section(title) or "—"
    except OSError:
        return "—"


def build_review_graph(context: ReviewContext):
    def fetch(state: ReviewState) -> dict:
        if state.get("previous"):  # révision : la page est déjà en base
            return {"page": state["previous"]["page"]}
        page = context.fetch(state["url"])
        return {"page": page.model_dump(mode="json")}

    def analyze(state: ReviewState) -> dict:
        page = Page.model_validate({k: v for k, v in state["page"].items() if k not in {"domains", "glossary"}})
        knowledge = context.knowledge()
        domains = knowledge.detect_domains(f"{page.title}\n{page.description}\n{page.text}")
        glossary = knowledge.glossary_terms_in(f"{page.title}\n{page.text}")
        rules = knowledge.rules_for(domains)
        numbered = "\n".join(f"[R{i}] ({domain}) {rule}" for i, (domain, rule) in enumerate(rules, 1)) or "Aucune."
        meta = (f"Titre : {page.title}\nSite : {page.site}\n"
                f"Date (métadonnées) : {page.published_at or 'non précisée'}\n"
                f"Domaines détectés : {', '.join(domains) or 'aucun'}")
        prompt = render_prompt(
            "review",
            skill=_skill_section(context.skill_path, "Méthode"),
            criteria=_skill_section(context.criteria_skill_path, "Critères de priorité"),
            rules=numbered,
            glossary=", ".join(e.title for e in glossary) or "aucun",
            memory=context.memory(),
            objections=sanitize_untrusted(state.get("objections") or "Aucune (première version).", 4000),
            meta=meta,
            article=sanitize_untrusted(page.text, settings.review_max_chars),
        )
        draft = context.llm.generate(prompt, ReviewDraft)
        return {"draft": draft.model_dump(), "domains": domains, "rules": [list(r) for r in rules],
                "glossary": [{"id": e.id, "title": e.title} for e in glossary]}

    def guard(state: ReviewState) -> dict:
        page = Page.model_validate({k: v for k, v in state["page"].items() if k not in {"domains", "glossary"}})
        analysis, warnings = guard_review(ReviewDraft.model_validate(state["draft"]), page,
                                          [tuple(r) for r in state["rules"]])
        if page.truncated:
            warnings.append(f"Article tronqué à {settings.review_max_chars} caractères pour l'analyse.")
        return {"analysis": analysis.model_dump(), "warnings": warnings}

    def save(state: ReviewState) -> dict:
        previous = state.get("previous") or {}
        page = state["page"] | {"domains": state["domains"], "glossary": state["glossary"]}
        record = ReviewRecord(  # validation complète avant persistance
            id=previous.get("id") or str(uuid4()),
            url=page["url"],
            title=page["title"],
            conversation_id=previous.get("conversation_id") or str(uuid4()),
            revision=previous.get("revision", 0) + 1,
            model=context.llm.model,
            page=page,
            analysis=state["analysis"],
            warnings=state["warnings"],
            rules=[f"({domain}) {rule}" for domain, rule in state["rules"]],
            objections=state.get("objections", ""),
        )
        data = record.model_dump(mode="json")
        storage.save_review(context.connection, data)
        return {"record": data, "review_id": record.id}

    builder = StateGraph(ReviewState)
    for name, node in [("fetch", fetch), ("analyze", analyze), ("guard", guard), ("save", save)]:
        builder.add_node(name, node)
    builder.add_edge(START, "fetch")
    builder.add_edge("fetch", "analyze")
    builder.add_edge("analyze", "guard")
    builder.add_edge("guard", "save")
    builder.add_edge("save", END)
    return builder.compile()


def step_detail(node: str, update: dict) -> str:
    if node == "fetch" and "page" in update:
        page = update["page"]
        return f"{page['site']} · {page['word_count']} mots" + (" (tronqué)" if page.get("truncated") else "")
    if node == "analyze" and "draft" in update:
        return (f"domaines : {', '.join(update['domains']) or 'aucun'} · {len(update['rules'])} règle(s) · "
                f"{len(update['draft']['claims'])} affirmation(s)")
    if node == "guard" and "analysis" in update:
        return f"{len(update['warnings'])} correction(s)"
    if node == "save" and "record" in update:
        return f"révision {update['record']['revision']}"
    return ""


def stream_review(graph, inputs: dict, config: dict | None = None):
    """Événements pour l'interface : step (un par nœud), puis review (record validé)."""
    steps, record = [], None
    last = datetime.now(timezone.utc)
    for update in graph.stream(inputs, config or {}, stream_mode="updates"):
        now = datetime.now(timezone.utc)
        for node, values in update.items():
            step = {"graph": "review", "node": node, "ms": round((now - last).total_seconds() * 1000),
                    "detail": step_detail(node, values or {})}
            steps.append(step)
            yield {"type": "step", **step}
            record = (values or {}).get("record", record)
        last = now
    yield {"type": "review", "review": record, "steps": steps}


def transcript_objections(messages: list) -> str:
    """Objections de l'utilisateur tirées du chat de challenge (réponses de l'assistant incluses)."""
    lines = []
    for message in messages[-12:]:
        role = "Utilisateur" if message.type == "human" else "Assistant"
        lines.append(f"{role} : {str(message.content)[:600]}")
    return "\n".join(lines)


def best_passages(text: str, query: str, limit: int = 4, max_chars: int = 3500) -> list[str]:
    """Paragraphes de l'article les plus proches de la question (recouvrement de mots)."""
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    wanted = {w for w in tokens(query) if len(w) > 2}
    ranked = sorted(range(len(paragraphs)),
                    key=lambda i: (-len(wanted & set(tokens(paragraphs[i]))), i))
    chosen = sorted(set(ranked[:limit]) | {0})
    passages, size = [], 0
    for index in chosen:
        passage = paragraphs[index][:1200]
        if size + len(passage) > max_chars:
            break
        passages.append(passage)
        size += len(passage)
    return passages


def record_as_text(record: dict) -> str:
    """Synthèse lisible (contexte du chat de challenge)."""
    analysis = record["analysis"]
    claims = "\n".join(
        f"- {c['claim']} ({'étayée' if c['status'] == 'etaye' else 'NON étayée'})" + (f" « {c['quote']} »" if c["quote"] else "")
        for c in analysis["claims"]
    ) or "- aucune"
    checks = "\n".join(f"- R{c['rule_id']} {c['rule']} → {c['verdict']} {c['note']}" for c in analysis["rule_checks"]) or "- aucune"
    return (
        f"Synthèse (révision {record['revision']}) : {analysis['summary']}\n"
        f"Pourquoi c'est important : {analysis['why_it_matters']}\n"
        f"Source {analysis['source_type']} : {analysis['source_type_reason']}\n"
        f"Scores : pertinence {analysis['relevance']}/10, nouveauté {analysis['novelty']}/10, "
        f"confiance {analysis['confidence']}/10\n"
        f"Affirmations :\n{claims}\nRègles métiers :\n{checks}\n"
        f"Risques : {'; '.join(analysis['risks']) or 'aucun'}"
    )


def dumps(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, indent=2)
