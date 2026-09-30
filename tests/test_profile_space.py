"""« Mon profil » : personnaliser et réinitialiser le profil qui oriente « Quoi de neuf ? »."""

import tomllib

import pytest
from langgraph.store.memory import InMemoryStore
from pydantic import ValidationError

from app import storage
from app.chat.tools import ChatServices, build_tools
from app.config import PROJECT_ROOT
from app.memory import WatchMemory
from app.profile import (Identity, ProfileEdit, ProfileError, active_profile_path, dump_toml, load_active_profile,
                         load_profile, personalize, profile_history, reset_profile, save_profile)

DEFAULT = PROJECT_ROOT / "profiles/julien.toml"


@pytest.fixture
def paths(tmp_path):
    return DEFAULT, tmp_path / "data" / "profile.toml", tmp_path / "data" / "profile-history"


def edit(**changes) -> ProfileEdit:
    base = {"role": "Chercheuse en robotique", "topics": ["robotics", "VLA"], "gpus": [], "platforms": ["Jetson"],
            "favor": ["open source"], "avoid": ["webinar"], "match": {"VLA": ["vla", "vision-language-action"]}}
    return ProfileEdit.model_validate(base | changes)


# --- Surcharge hors Git : enregistrer, relire, réinitialiser -----------------------------------


def test_saving_writes_an_override_and_never_touches_the_repository_profile(paths):
    default, override, history = paths
    before = DEFAULT.read_bytes()
    assert active_profile_path(default, override) == default

    saved = save_profile(edit(), default, override, history)

    assert DEFAULT.read_bytes() == before  # le profil versionné dans Git n'est jamais modifié
    assert active_profile_path(default, override) == override
    assert saved.name == "julien" and saved.version == load_profile(default).version + 1
    assert saved.priorities.topics == ["robotics", "VLA"] and saved.preferences.avoid == ["webinar"]
    assert load_active_profile(default, override).label == saved.label != load_profile(default).label

    again = save_profile(edit(topics=["humanoids"]), default, override, history)
    assert again.version == saved.version + 1
    assert len(profile_history(history)) == 1  # la version remplacée est archivée
    assert again.match == {}  # les termes d'un libellé retiré disparaissent


def test_reset_returns_to_the_repository_profile_and_archives_the_override(paths):
    default, override, history = paths
    assert reset_profile(override, history) is False
    save_profile(edit(), default, override, history)

    assert reset_profile(override, history) is True
    assert not override.exists() and active_profile_path(default, override) == default
    assert load_active_profile(default, override).label == load_profile(default).label
    assert len(profile_history(history)) == 1


def test_toml_round_trip_survives_quotes_accents_and_backslashes():
    profile = load_profile(DEFAULT).model_copy(update={"identity": Identity(role='Ingé "LLM" \\ édge\tlocal')})
    reloaded = tomllib.loads(dump_toml(profile))
    assert reloaded["identity"]["role"] == 'Ingé "LLM" \\ édge\tlocal'
    assert reloaded["match"]["GPU optimization"] == profile.match["GPU optimization"]


def test_edit_is_bounded_deduplicated_and_rejects_unknown_fields():
    assert edit(topics=["vLLM", "vllm", " MCP "]).topics == ["vLLM", "MCP"]
    with pytest.raises(ValidationError):
        edit(topics=["x" * 81])
    with pytest.raises(ValidationError):
        edit(topics=[f"t{i}" for i in range(41)])
    with pytest.raises(ValidationError):
        ProfileEdit.model_validate({"name": "pirate"})  # nom et version ne se modifient pas
    with pytest.raises(ValidationError):
        edit(match={"VLA": [f"t{i}" for i in range(21)]})
    assert issubclass(ProfileError, ValueError)


# --- « Quoi de neuf ? » orienté par le profil -----------------------------------------------------


ITEMS = [
    {"title": "Company webinar on AI strategy", "summary": "Join our webinar.", "url": "https://a.org/1"},
    {"title": "New VLA model for robotics", "summary": "Open source weights, runs on Jetson.", "url": "https://a.org/2"},
    {"title": "Diffusion paper", "summary": "Image generation study.", "url": "https://a.org/3"},
    {"title": "Humanoid robotics dataset", "summary": "A dataset.", "url": "https://a.org/4"},
]


def test_personalize_puts_priorities_first_and_what_to_avoid_last(paths):
    default, override, history = paths
    profile = save_profile(edit(), default, override, history)

    ranked = personalize(ITEMS, profile)

    assert [i["url"][-1] for i in ranked] == ["2", "4", "3", "1"]
    top = ranked[0]["for_you"]
    assert top["score"] > ranked[1]["for_you"]["score"] > 0 and not top["avoid"]
    assert top["reasons"][0] == "tes priorités : robotics, VLA"
    assert ranked[-1]["for_you"] == {"score": 0, "reasons": ["à éviter selon ton profil : webinar"], "avoid": True}
    assert ranked[2]["for_you"]["score"] == 0  # sans lien : ordre d'origine conservé


def test_grill_me_keywords_and_exclusions_orient_the_order():
    ranked = personalize(ITEMS, None, keywords=["diffusion"], exclusions=["dataset"])
    assert [i["url"][-1] for i in ranked] == ["3", "1", "2", "4"]
    assert ranked[0]["for_you"]["reasons"] == ["tes mots-clés : diffusion"]
    assert ranked[-1]["for_you"]["avoid"] and "tu as demandé d'écarter : dataset" in ranked[-1]["for_you"]["reasons"]


def test_latest_digests_tool_ranks_signals_for_the_reader(connection, tmp_path, paths):
    default, override, history = paths
    profile = save_profile(edit(), default, override, history)
    item = lambda n, title: {"title": title, "url": f"https://a.org/{n}", "source": "rss", "summary": title,  # noqa: E731
                             "why_it_matters": "Important.", "tags": []}
    storage.record_digest(connection, "r1", {"generated_at": "2026-09-28T08:00:00+00:00", "executive_summary": "S1",
                                             "items": [item(1, "Partner webinar"), item(2, "Diffusion paper")]},
                          tmp_path / "a.md", tmp_path / "a.json")
    storage.record_digest(connection, "r2", {"generated_at": "2026-09-29T08:00:00+00:00", "executive_summary": "S2",
                                             "items": [item(3, "VLA robotics release"), item(2, "Diffusion paper")]},
                          tmp_path / "b.md", tmp_path / "b.json")
    store = InMemoryStore()
    WatchMemory(store).edit_interests("Robotique", ["diffusion"], [])
    services = ChatServices(connection=connection, store=store, reports_dir=tmp_path, profile=lambda: profile)

    result = build_tools(services)["latest_digests"].invoke({})

    assert [s["url"] for s in result["sources"]] == ["https://a.org/3", "https://a.org/2", "https://a.org/1"]
    assert result["sources"][-1]["for_you"]["avoid"] is True
    assert profile.label in result["summary"] and "3 signal(aux)" in result["summary"]
    assert "Profil du lecteur" in result["context"] and "Priorités : robotics, VLA" in result["context"]
    assert "Pour toi : pertinence pour toi" in result["context"] and "à éviter selon ton profil" in result["context"]
    assert result["data"] == {"profile": profile.label}


# --- Centres d'intérêt Grill-me : retouche et oubli --------------------------------------------


def test_interests_can_be_edited_then_cleared():
    memory = WatchMemory(InMemoryStore())
    assert memory.clear_interests() is False
    memory.set_interests({"summary": "Avant", "keywords": ["x"], "exclusions": [], "frequency": "quotidien",
                          "answered_at": "2026-09-01T00:00:00+00:00"})

    edited = memory.edit_interests("Après", ["vllm", "sglang"], ["marketing"])

    assert memory.interests() == edited and edited["frequency"] == "quotidien" and edited["edited"] is True
    assert "Mots-clés à privilégier : vllm, sglang" in memory.prompt_context()
    assert memory.clear_interests() is True and memory.interests() is None
    assert "Grill-me" not in memory.prompt_context()
