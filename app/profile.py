"""Profil d'impact versionné (profiles/*.toml) : « pourquoi c'est important pour moi ? ».

Le profil est un fichier TOML suivi par Git (versionné), validé par Pydantic. Il sert trois fois :

- ranking hybride : composante « profil » (priorités, matériel, préférences) et pénalité « à éviter » ;
- Editor : rôle et matériel du lecteur, pour un `why_it_matters` concret ;
- digest : raisons d'impact par item et version du profil utilisé (audit).

Le repérage est déterministe (mots entiers dans titre, résumé et tags) : aucun LLM ici.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field


class Identity(BaseModel):
    role: str = ""
    language: str = "fr"


class Priorities(BaseModel):
    topics: list[str] = Field(default_factory=list)


class Hardware(BaseModel):
    gpus: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)


class Preferences(BaseModel):
    favor: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)


class ImpactProfile(BaseModel):
    name: str
    version: int = Field(1, ge=1)
    identity: Identity = Field(default_factory=Identity)
    priorities: Priorities = Field(default_factory=Priorities)
    hardware: Hardware = Field(default_factory=Hardware)
    preferences: Preferences = Field(default_factory=Preferences)
    match: dict[str, list[str]] = Field(default_factory=dict)  # libellé → termes recherchés
    fingerprint: str = ""  # empreinte du fichier, calculée au chargement

    @property
    def label(self) -> str:
        return f"{self.name}@v{self.version}#{self.fingerprint}"

    def terms(self, label: str) -> list[str]:
        return [t.lower() for t in self.match.get(label) or [label]]

    def prompt_text(self) -> str:
        lines = [f"- Rôle : {self.identity.role}"] if self.identity.role else []
        if self.priorities.topics:
            lines.append("- Priorités : " + ", ".join(self.priorities.topics))
        if hardware := [*self.hardware.gpus, *self.hardware.platforms]:
            lines.append("- Matériel : " + ", ".join(hardware))
        if self.preferences.favor:
            lines.append("- Privilégie : " + ", ".join(self.preferences.favor))
        if self.preferences.avoid:
            lines.append("- Évite : " + ", ".join(self.preferences.avoid))
        return "\n".join(lines) or "Aucun profil déclaré."


def load_profile(path: Path | None) -> ImpactProfile | None:
    """Profil validé, ou None si le fichier est absent (le ranking garde alors ses mots-clés)."""
    if path is None or not path.is_file():
        return None
    raw = path.read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    data.setdefault("name", path.stem)
    return ImpactProfile.model_validate(data | {"fingerprint": hashlib.sha256(raw).hexdigest()[:8]})


@dataclass
class Impact:
    topics: list[str] = field(default_factory=list)
    hardware: list[str] = field(default_factory=list)
    favor: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        """0-1 : priorités (0,6), matériel (0,2), préférences (0,2) ; « à éviter » est une pénalité à part."""
        return (0.6 * min(1.0, len(self.topics) / 2) + 0.2 * bool(self.hardware)
                + 0.2 * min(1.0, len(self.favor) / 2))

    def reasons(self) -> list[str]:
        reasons = []
        if self.topics:
            reasons.append("tes priorités : " + ", ".join(self.topics[:4]))
        if self.hardware:
            reasons.append("ton matériel : " + ", ".join(self.hardware[:3]))
        if self.favor:
            reasons.append("tes préférences : " + ", ".join(self.favor[:3]))
        if self.avoid:
            reasons.append("à éviter selon ton profil : " + ", ".join(self.avoid[:3]))
        return reasons


def _matches(terms: list[str], text: str) -> bool:
    return any(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) for term in terms)


def assess(profile: ImpactProfile | None, text: str) -> Impact:
    """Libellés du profil retrouvés dans un texte (titre, résumé, tags)."""
    if profile is None:
        return Impact()
    text = text.lower()
    found = lambda labels: [label for label in labels if _matches(profile.terms(label), text)]  # noqa: E731
    return Impact(
        topics=found(profile.priorities.topics),
        hardware=found([*profile.hardware.gpus, *profile.hardware.platforms]),
        favor=found(profile.preferences.favor),
        avoid=found(profile.preferences.avoid),
    )
