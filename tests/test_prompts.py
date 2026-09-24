"""Prompts éditables : validation, surcharge, historique, rendu, réinitialisation."""

import pytest

from app.config import settings
from app.harness.prompts import (
    PromptError, load_prompt, prompt_path, render_prompt, reset_prompt, save_prompt, validate_prompt,
)
from app.instructions import render_instructions


@pytest.fixture(autouse=True)
def overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "prompt_overrides_dir", tmp_path / "prompts")


def test_override_is_used_then_reset():
    default = load_prompt("critic")["text"]

    save_prompt("critic", "Sois très strict.\n$signals")

    assert render_prompt("critic", signals="[1] X") == "Sois très strict.\n[1] X"
    assert prompt_path("critic").parent == settings.prompt_overrides_dir
    reset_prompt("critic")
    assert load_prompt("critic")["text"] == default


def test_history_keeps_previous_versions():
    save_prompt("router", "v1 $message")
    save_prompt("router", "v2 $message $history")
    history = load_prompt("router")["history"]
    assert len(history) == 2 and all(name.startswith("router-") for name in history)


@pytest.mark.parametrize(("name", "text", "error"), [
    ("scout", "Pas de documents", "documents"),
    ("scout", "$documents $inconnu", "inconnue"),
    ("chat-system", "Contexte : {{ context }} {{ secret }}", "secret"),
    ("chat-system", "{{ context ", "Syntaxe Jinja"),
    ("editor", "   ", "vide"),
    ("nope", "$x", "inconnu"),
])
def test_invalid_prompts_are_rejected(name, text, error):
    with pytest.raises(PromptError, match=error):
        validate_prompt(name, text)


def test_escaped_dollar_is_not_a_variable():
    validate_prompt("critic", "Coût : $$5 par appel.\n$signals")


def test_chat_system_override_is_rendered_by_instructions():
    save_prompt("chat-system", "Assistant {{ veille.nom }}. Réponds brièvement.\n{{ context }}")
    text = render_instructions("chat", context="[1] Doc")
    assert text.startswith("Assistant Veille LLM / GenAI. Réponds brièvement.") and "[1] Doc" in text
