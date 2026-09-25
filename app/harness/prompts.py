"""Prompts des agents : versions par défaut (app/prompts, Git) et surcharges éditables.

Une surcharge enregistrée depuis l'interface va dans `PROMPT_OVERRIDES_DIR`
(data/prompts) avec un historique horodaté ; les fichiers suivis par Git ne
sont jamais modifiés. `reset_prompt` supprime la surcharge.
"""

from datetime import datetime, timezone
from pathlib import Path
from string import Template

from jinja2 import Environment, TemplateSyntaxError, meta

from app.config import settings

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# nom → (fichier par défaut, moteur, variables obligatoires, variables autorisées en plus)
EDITABLE: dict[str, tuple[Path, str, set[str], set[str]]] = {
    "scout": (PROMPTS_DIR / "scout.md", "template", {"documents"}, {"criteria", "memory", "max_picks"}),
    "critic": (PROMPTS_DIR / "critic.md", "template", {"signals"}, set()),
    "claims": (PROMPTS_DIR / "claims.md", "template", {"signals"}, {"max_claims"}),
    "editor": (PROMPTS_DIR / "editor.md", "template", {"signals"}, {"memory", "corrections", "profile"}),
    "router": (PROMPTS_DIR / "router.md", "template", {"message"}, {"history"}),
    "grill": (PROMPTS_DIR / "grill.md", "template", {"answers"}, set()),
    "review": (PROMPTS_DIR / "review.md", "template", {"article"},
               {"skill", "criteria", "rules", "glossary", "memory", "objections", "meta"}),
    "news-search": (PROMPTS_DIR / "news_search.md", "template", {"topics"},
                    {"days", "today", "exclusions", "memory"}),
    "events-search": (PROMPTS_DIR / "events_search.md", "template", {"topics"}, {"today", "horizon", "memory"}),
    "media-search": (PROMPTS_DIR / "media_search.md", "template", {"topics"}, {"today", "days", "memory"}),
    "chat-system": (
        settings.templates_dir / "claude" / "chat-system.md.j2",
        "jinja",
        {"context"},
        {"veille", "themes", "sources", "format", "outils", "today", "model", "memory", "tool", "period"},
    ),
}
DESCRIPTIONS = {
    "scout": "Agent Scout (research) : sélection des documents par lot",
    "critic": "Agent Critic (review) : vérification factuelle des signaux",
    "claims": "Fact-checker (evidence) : extraction des affirmations et de leurs citations",
    "editor": "Agent Editor (editorial) : rédaction du digest",
    "router": "Routeur du chat : choix de l'outil",
    "grill": "Grill-me : synthèse de l'entretien sur tes centres d'intérêt",
    "review": "Agent Reviewer : review d'une actualité à partir de son URL",
    "news-search": "Scout web : recherche d'actus par l'API Claude (outil web_search)",
    "events-search": "Scout événements : calendrier IA par l'API Claude (dates vérifiées dans la page)",
    "media-search": "Scout médias : vidéos et podcasts IA par l'API Claude (outil web_search)",
    "chat-system": "Prompt système de l'assistant de chat (Jinja2)",
}


class PromptError(ValueError):
    pass


def override_path(name: str) -> Path:
    default = EDITABLE[name][0]
    return settings.prompt_overrides_dir / default.name


def prompt_path(name: str) -> Path:
    override = override_path(name)
    return override if override.exists() else EDITABLE[name][0]


def variables(text: str, engine: str) -> set[str]:
    if engine == "jinja":
        return set(meta.find_undeclared_variables(Environment().parse(text)))
    found = (m.group("named") or m.group("braced") for m in Template.pattern.finditer(text))
    return {name for name in found if name}


def validate_prompt(name: str, text: str) -> None:
    if name not in EDITABLE:
        raise PromptError(f"Prompt inconnu : {name}")
    _, engine, required, optional = EDITABLE[name]
    if not text.strip():
        raise PromptError("Le prompt est vide.")
    if len(text) > 20000:
        raise PromptError("Prompt trop long (20 000 caractères maximum).")
    try:
        found = variables(text, engine)
    except TemplateSyntaxError as error:
        raise PromptError(f"Syntaxe Jinja invalide : {error}") from error
    if missing := required - found:
        raise PromptError("Variable(s) obligatoire(s) absente(s) : " + ", ".join(sorted(missing)))
    if unknown := found - required - optional:
        raise PromptError("Variable(s) inconnue(s) : " + ", ".join(sorted(unknown)))


def load_prompt(name: str) -> dict:
    if name not in EDITABLE:
        raise PromptError(f"Prompt inconnu : {name}")
    default, engine, required, optional = EDITABLE[name]
    return {
        "name": name,
        "description": DESCRIPTIONS[name],
        "engine": engine,
        "text": prompt_path(name).read_text(encoding="utf-8"),
        "default": default.read_text(encoding="utf-8"),
        "overridden": override_path(name).exists(),
        "required": sorted(required),
        "optional": sorted(optional),
        "history": [p.name for p in sorted(history_dir().glob(f"{name}-*"), reverse=True)[:10]],
    }


def history_dir() -> Path:
    return settings.prompt_overrides_dir / "history"


def save_prompt(name: str, text: str) -> dict:
    validate_prompt(name, text)
    target = override_path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    history_dir().mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    (history_dir() / f"{name}-{stamp}{target.suffix}").write_text(
        prompt_path(name).read_text(encoding="utf-8"), encoding="utf-8"
    )
    target.write_text(text, encoding="utf-8")
    return load_prompt(name)


def reset_prompt(name: str) -> dict:
    if name not in EDITABLE:
        raise PromptError(f"Prompt inconnu : {name}")
    override_path(name).unlink(missing_ok=True)
    return load_prompt(name)


def render_prompt(name: str, **values: object) -> str:
    template = Template(prompt_path(name).read_text(encoding="utf-8"))
    # substitute (et non safe_substitute) : une variable oubliée doit échouer.
    return template.substitute({key: str(value) for key, value in values.items()})


def prompt_names() -> list[str]:
    return list(EDITABLE)
