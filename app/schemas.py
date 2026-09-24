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


class Critique(BaseModel):
    signal_url: HttpUrl
    verdict: Literal["keep", "drop", "needs_review"]
    rationale: str
    factual_risks: list[str] = Field(default_factory=list)
    duplicate_of: str | None = None


class DigestItem(BaseModel):
    title: str
    source: str
    url: HttpUrl
    date: datetime | None = None
    summary: str
    why_it_matters: str
    tags: list[str] = Field(default_factory=list)


class Digest(BaseModel):
    generated_at: datetime
    period_label: str
    executive_summary: str
    items: list[DigestItem]
    rejected_count: int = 0


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


class EditorItem(BaseModel):
    signal_id: int
    summary: str = Field(description="Deux phrases maximum, en français")
    why_it_matters: str


class EditorOutput(BaseModel):
    executive_summary: str
    items: list[EditorItem]
