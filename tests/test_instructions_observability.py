"""Instructions Claude templatées et configuration de traçage (Langfuse, LangSmith)."""

import os

import pytest

from app import observability
from app.config import settings
from app.instructions import TARGETS, load_profile, render_instructions


@pytest.mark.parametrize("target", sorted(TARGETS))
def test_every_target_renders_from_the_profile(target):
    text = render_instructions(target, period="semaine 39")
    profile = load_profile()
    assert profile["veille"]["nom"] in text
    assert all(theme in text for theme in profile["themes"]["prioritaires"])
    assert "{{" not in text and "{%" not in text


def test_claude_md_lists_tools_and_imports_memory():
    text = render_instructions("claude-md")
    assert "| notion-sync | `python -m app.main notion-sync` |" in text
    assert text.rstrip().endswith("@.claude/memory/veille.md")


def test_request_template_uses_period_and_exclusions():
    text = render_instructions("demande", period="semaine 39")
    assert "Période : semaine 39" in text
    assert "<exclusions>" in text and "outils offensifs" in text


def test_chat_prompt_embeds_runtime_values():
    text = render_instructions("chat", memory="- Écarter les tutoriels", tool="search_watch", context="[1] Doc")
    assert "- Écarter les tutoriels" in text and "search_watch" in text and text.rstrip().endswith("[1] Doc")


def test_unknown_target_is_refused():
    with pytest.raises(ValueError, match="Cible inconnue"):
        render_instructions("slides")


def test_trace_config_groups_calls_by_session(monkeypatch):
    monkeypatch.setattr(settings, "langfuse_enabled", False)
    monkeypatch.setattr(settings, "langsmith_tracing", False)

    config = observability.trace_config("conv-42", "chat")

    assert config["callbacks"] == [] and config["tags"] == ["chat"]
    metadata = config["metadata"]
    assert metadata["langfuse_session_id"] == metadata["session_id"] == "conv-42"
    assert metadata["langfuse_tags"] == ["chat"]


def test_langsmith_is_enabled_through_process_environment(monkeypatch):
    for key in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT"):
        # setenv puis delenv : monkeypatch mémorise l'état d'origine et le restaure
        # après le test, même si setup_langsmith() écrit dans os.environ.
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    monkeypatch.setattr(settings, "langsmith_tracing", True)
    monkeypatch.setattr(settings, "langsmith_api_key", None)
    assert observability.setup_langsmith() is False  # clé absente : rien n'est activé
    assert "LANGSMITH_TRACING" not in os.environ

    monkeypatch.setattr(settings, "langsmith_api_key", "lsv2_test")
    assert observability.setup_langsmith() is True
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_PROJECT"] == settings.langsmith_project
    assert observability.status()["langsmith"] is True


def test_langfuse_requires_keys(monkeypatch):
    monkeypatch.setattr(settings, "langfuse_enabled", True)
    monkeypatch.setattr(settings, "langfuse_public_key", None)
    assert observability.langfuse_callbacks() == []
    assert observability.status()["langfuse"] is False


def test_langsmith_environment_does_not_leak_between_tests():
    assert os.environ.get("LANGSMITH_API_KEY") != "lsv2_test"
