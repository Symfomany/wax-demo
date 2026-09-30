"""Profil d'impact versionné (profiles/*.toml) : « pourquoi c'est important pour moi ? ».

Le profil est un fichier TOML suivi par Git (versionné), validé par Pydantic. Il sert trois fois :

- ranking hybride : composante « profil » (priorités, matériel, préférences) et pénalité « à éviter » ;
- Editor : rôle et matériel du lecteur, pour un `why_it_matters` concret ;
- digest : raisons d'impact par item et version du profil utilisé (audit).

Le repérage est déterministe (mots entiers dans titre, résumé et tags) : aucun LLM ici.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


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


# --- Profil actif : défaut versionné (Git) ou surcharge éditée depuis l'interface ---------------
#
# Comme les prompts (data/prompts), une personnalisation n'écrit jamais le fichier suivi par Git :
# elle va dans `IMPACT_PROFILE_OVERRIDE_PATH` (data/profile.toml, hors Git), relue par tomllib et
# validée par Pydantic avant d'être gardée. Réinitialiser = supprimer la surcharge (archivée).


class ProfileError(ValueError):
    pass


Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


def _clean(values: list[str]) -> list[str]:
    """Sans doublons (insensible à la casse), dans l'ordre saisi."""
    seen, kept = set(), []
    for value in values:
        if value.lower() not in seen:
            seen.add(value.lower())
            kept.append(value)
    return kept


class ProfileEdit(BaseModel):
    """Ce que l'interface peut modifier : bornes strictes, jamais le nom ni la version."""

    model_config = ConfigDict(extra="forbid")

    role: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    topics: list[Label] = Field(default_factory=list, max_length=40)
    gpus: list[Label] = Field(default_factory=list, max_length=20)
    platforms: list[Label] = Field(default_factory=list, max_length=20)
    favor: list[Label] = Field(default_factory=list, max_length=40)
    avoid: list[Label] = Field(default_factory=list, max_length=40)
    match: dict[Label, list[Label]] = Field(default_factory=dict, max_length=80)

    @field_validator("topics", "gpus", "platforms", "favor", "avoid")
    @classmethod
    def _dedupe(cls, values: list[str]) -> list[str]:
        return _clean(values)

    @field_validator("match")
    @classmethod
    def _terms(cls, match: dict[str, list[str]]) -> dict[str, list[str]]:
        if any(len(terms) > 20 for terms in match.values()):
            raise ValueError("20 termes au plus par libellé")
        return {label: _clean(terms) for label, terms in match.items() if terms}


def _settings():
    from app.config import settings

    return settings


def active_profile_path(default: Path | None = None, override: Path | None = None) -> Path:
    default = default or _settings().impact_profile_path
    override = override or _settings().impact_profile_override_path
    return override if override.is_file() else default


def load_active_profile(default: Path | None = None, override: Path | None = None) -> ImpactProfile | None:
    """Profil utilisé par le ranking, l'Editor et « Quoi de neuf ? » : la surcharge si elle existe."""
    return load_profile(active_profile_path(default, override))


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)  # échappements JSON = chaînes de base TOML valides


def _toml_list(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(v) for v in values) + "]"


def dump_toml(profile: ImpactProfile) -> str:
    lines = [
        "# Profil personnalisé depuis l'interface (hors Git) ; réinitialiser = revenir au profil du dépôt.",
        f"name = {_toml_string(profile.name)}",
        f"version = {profile.version}",
        "",
        "[identity]",
        f"role = {_toml_string(profile.identity.role)}",
        f"language = {_toml_string(profile.identity.language)}",
        "",
        "[priorities]",
        f"topics = {_toml_list(profile.priorities.topics)}",
        "",
        "[hardware]",
        f"gpus = {_toml_list(profile.hardware.gpus)}",
        f"platforms = {_toml_list(profile.hardware.platforms)}",
        "",
        "[preferences]",
        f"favor = {_toml_list(profile.preferences.favor)}",
        f"avoid = {_toml_list(profile.preferences.avoid)}",
        "",
        "[match]",
        *(f"{_toml_string(label)} = {_toml_list(terms)}" for label, terms in profile.match.items()),
    ]
    return "\n".join(lines) + "\n"


def _archive(override: Path, history_dir: Path) -> None:
    if override.is_file():
        history_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        (history_dir / f"profile-{stamp}.toml").write_bytes(override.read_bytes())


def profile_from_edit(edit: ProfileEdit, current: ImpactProfile | None) -> ImpactProfile:
    """Profil saisi (non enregistré) : même nom, version suivante — sert aussi à l'aperçu."""
    labels = {*edit.topics, *edit.gpus, *edit.platforms, *edit.favor, *edit.avoid}
    return ImpactProfile(
        name=current.name if current else "profil",
        version=(current.version + 1) if current else 1,
        identity=Identity(role=edit.role, language=current.identity.language if current else "fr"),
        priorities=Priorities(topics=edit.topics),
        hardware=Hardware(gpus=edit.gpus, platforms=edit.platforms),
        preferences=Preferences(favor=edit.favor, avoid=edit.avoid),
        # Les termes d'un libellé retiré n'ont plus d'objet.
        match={label: terms for label, terms in edit.match.items() if label in labels},
    )


def save_profile(edit: ProfileEdit, default: Path, override: Path, history_dir: Path) -> ImpactProfile:
    """Écrit la surcharge (version + 1), relue par tomllib et validée avant d'être gardée."""
    profile = profile_from_edit(edit, load_profile(active_profile_path(default, override)))
    text = dump_toml(profile)
    try:
        reloaded = ImpactProfile.model_validate(tomllib.loads(text))
    except (tomllib.TOMLDecodeError, ValueError) as error:
        raise ProfileError(f"Profil invalide : {error}") from error
    if reloaded.model_dump(exclude={"fingerprint"}) != profile.model_dump(exclude={"fingerprint"}):
        raise ProfileError("Le profil relu diffère du profil saisi.")
    _archive(override, history_dir)
    override.parent.mkdir(parents=True, exist_ok=True)
    temporary = override.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(override)
    return load_profile(override)


def reset_profile(override: Path, history_dir: Path) -> bool:
    """Supprime la surcharge (archivée d'abord) : retour au profil versionné du dépôt."""
    if not override.is_file():
        return False
    _archive(override, history_dir)
    override.unlink()
    return True


def profile_history(history_dir: Path, limit: int = 10) -> list[str]:
    if not history_dir.is_dir():
        return []
    return sorted((p.name for p in history_dir.glob("profile-*.toml")), reverse=True)[:limit]


# --- « Quoi de neuf ? » orienté par le profil ---------------------------------------------------


def personalize(items: list[dict], profile: ImpactProfile | None, keywords: list[str] = (),
                exclusions: list[str] = ()) -> list[dict]:
    """Items classés pour le lecteur, avec un score /100 et ses raisons (déterministe, sans LLM).

    Profil d'impact (priorités, matériel, préférences) + mots-clés Grill-me ; un item qui touche
    « à éviter » ou une exclusion Grill-me passe en fin de liste. À score égal, l'ordre d'origine
    (le plus récent d'abord) est conservé.
    """
    ranked = []
    for index, item in enumerate(items):
        text = " ".join([item.get("title", ""), item.get("summary", ""), " ".join(item.get("tags") or [])]).lower()
        impact = assess(profile, text)
        wanted = [k for k in keywords if _matches([k.lower()], text)]
        excluded = [k for k in exclusions if _matches([k.lower()], text)]
        avoid = bool(impact.avoid or excluded)
        score = round(100 * min(1.0, impact.score * 0.75 + 0.125 * len(wanted)))
        reasons = impact.reasons()
        if wanted:
            reasons.append("tes mots-clés : " + ", ".join(wanted[:4]))
        if excluded:
            reasons.append("tu as demandé d'écarter : " + ", ".join(excluded[:3]))
        ranked.append((avoid, -score, index, item | {"for_you": {"score": 0 if avoid else score,
                                                                  "reasons": reasons, "avoid": avoid}}))
    return [entry[-1] for entry in sorted(ranked, key=lambda entry: entry[:3])]
