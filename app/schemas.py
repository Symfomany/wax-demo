from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class Document(BaseModel):
    source: Literal["rss", "arxiv", "github"]
    title: str
    url: HttpUrl
    published_at: datetime | None = None
    summary: str = ""
    content: str = ""
    tags: list[str] = Field(default_factory=list)


class Signal(BaseModel):
    title: str
    url: HttpUrl
    source: str
    published_at: datetime | None = None
    relevance: int = Field(ge=0, le=10)
    novelty: int = Field(ge=0, le=10)
    confidence: int = Field(ge=0, le=10)
    why_it_matters: str
    claims: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    # Ranking hybride explicable (app/workflow/quality.py), calculé par le code
    score: float | None = Field(None, ge=0, le=100)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    rank_reasons: list[str] = Field(default_factory=list)
    impact_reasons: list[str] = Field(default_factory=list)  # profil d'impact (app/profile.py)


class Critique(BaseModel):
    signal_url: HttpUrl
    verdict: Literal["keep", "drop", "needs_review"]
    rationale: str
    factual_risks: list[str] = Field(default_factory=list)
    duplicate_of: str | None = None


# --- Claims et preuves (sous-graphe evidence, app/workflow/evidence.py) -------------

# fait, chiffre, benchmark, annonce : affirmations vérifiables ; analyse, hypothese : interprétations
ClaimKind = Literal["fait", "chiffre", "benchmark", "annonce", "analyse", "hypothese"]
# confirme : citation retrouvée dans une source primaire (protocole complet pour un benchmark) ;
# rapporte : citation retrouvée, mais source secondaire seule ou protocole incomplet ;
# conteste : contredit par une autre source ; non_etaye : citation introuvable (jamais publié)
EvidenceStatus = Literal["confirme", "rapporte", "conteste", "non_etaye"]
FACT_KINDS = ("fait", "chiffre", "benchmark", "annonce")
PROTOCOL_FIELDS = ("hardware", "model", "batch", "context", "version", "method")


class BenchmarkProtocol(BaseModel):
    """Protocole d'un benchmark ; chaque valeur est recopiée de la source (sinon vide)."""

    hardware: str = ""
    model: str = ""
    batch: str = ""
    context: str = ""
    version: str = ""
    method: str = ""


class Evidence(BaseModel):
    url: HttpUrl
    source: str
    quote: str
    primary: bool = True


class Claim(BaseModel):
    id: str  # « S1-C2 » : signal 1, claim 2
    signal_url: HttpUrl
    text: str
    kind: ClaimKind
    status: EvidenceStatus = "non_etaye"
    evidence: list[Evidence] = Field(default_factory=list)  # la première preuve est la citation d'origine
    benchmark: BenchmarkProtocol | None = None
    missing_protocol: list[str] = Field(default_factory=list)
    contradicts: list[str] = Field(default_factory=list)  # URLs des sources contradictoires
    confidence: int = Field(0, ge=0, le=100)
    reasons: list[str] = Field(default_factory=list)


class Contradiction(BaseModel):
    claim_id: str
    claim: str
    quote: str
    other_url: HttpUrl
    other_quote: str
    reason: str


class DigestItem(BaseModel):
    title: str
    source: str
    url: HttpUrl
    date: datetime | None = None
    summary: str
    why_it_matters: str
    tags: list[str] = Field(default_factory=list)
    score: float | None = Field(None, ge=0, le=100)  # ranking hybride (0-100)
    rank_reasons: list[str] = Field(default_factory=list)
    # Sous-graphe evidence : faits étayés recopiés par le code, interprétations de l'Editor marquées
    facts: list[Claim] = Field(default_factory=list)
    analysis: str = ""
    hypothesis: str = ""
    confidence: int | None = Field(None, ge=0, le=100)
    confidence_reasons: list[str] = Field(default_factory=list)
    # Profil d'impact (profiles/*.toml) : « pourquoi c'est important pour moi »
    impact_reasons: list[str] = Field(default_factory=list)


class Digest(BaseModel):
    generated_at: datetime
    period_label: str
    executive_summary: str
    items: list[DigestItem]
    rejected_count: int = 0
    contradictions: list[Contradiction] = Field(default_factory=list)
    profile_version: str | None = None  # « nom@version#empreinte » du profil d'impact utilisé


# --- Sorties LLM -----------------------------------------------------------
# Le LLM ne manipule que des identifiants numériques : URL, titre, source et
# date sont recopiés par le code depuis les documents collectés.


class ScoutPick(BaseModel):
    doc_id: int = Field(description="Numéro [n] du document retenu")
    relevance: int = Field(ge=0, le=10)
    novelty: int = Field(ge=0, le=10)
    confidence: int = Field(ge=0, le=10)
    why_it_matters: str = Field(description="Une phrase, en français")
    tags: list[str] = Field(default_factory=list)


class ScoutOutput(BaseModel):
    picks: list[ScoutPick]


class CriticVerdict(BaseModel):
    signal_id: int
    verdict: Literal["keep", "drop", "needs_review"]
    rationale: str
    factual_risks: list[str] = Field(default_factory=list)


class CriticOutput(BaseModel):
    verdicts: list[CriticVerdict]


class ClaimDraft(BaseModel):
    signal_id: int
    text: str = Field(description="L'affirmation en une phrase, en français")
    kind: ClaimKind
    quote: str = Field("", description="Passage copié mot pour mot de l'extrait (250 caractères max)")
    benchmark: BenchmarkProtocol | None = Field(None, description="Seulement si kind = benchmark")


class ClaimsOutput(BaseModel):
    claims: list[ClaimDraft]


class EditorItem(BaseModel):
    signal_id: int
    summary: str = Field(description="Deux phrases maximum, en français, uniquement à partir des faits")
    why_it_matters: str
    analysis: str = Field("", description="Interprétation de l'Editor (une phrase), vide si aucune")
    hypothesis: str = Field("", description="Hypothèse non vérifiée (une phrase), vide si aucune")


class EditorOutput(BaseModel):
    executive_summary: str
    items: list[EditorItem]
