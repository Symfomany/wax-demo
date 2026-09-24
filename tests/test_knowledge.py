"""Base de connaissances : fichiers livrés, format Markdown, recherche, index, téléversements."""

import pytest

from app import knowledge
from app.config import settings
from app.knowledge import KnowledgeError, KnowledgeExists, load_knowledge, parse_markdown

NOTE = """---
title: Notes RAG maison
type: glossaire
---

## Late chunking
Domaine : RAG
Alias : chunking tardif
Mots-clés : embeddings, contexte
Source : https://arxiv.org/abs/2005.11401

Découper **après** l'encodage du document complet.
"""


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "knowledge_uploads_dir", tmp_path / "uploads")
    return tmp_path / "uploads"


def test_shipped_files_are_valid_and_sourced():
    base = load_knowledge()
    assert base.errors == []
    assert {f.name for f in base.files} >= {"glossaire.md", "regles-metiers.md", "prompts-veille.md"}
    glossary = base.of_kind("glossaire")
    assert len(glossary) >= 60
    assert all(str(url).startswith("https://") for e in glossary for url in e.sources)
    assert all(e.rules for e in base.of_kind("regles"))
    assert {e.target for e in base.of_kind("prompts")} == {"chat", "review"}
    assert "README.md" not in {f.name for f in base.files}


def test_entry_metadata_and_free_note():
    info, entries = parse_markdown(NOTE, "notes-rag.md")
    assert (info.kind, info.title, info.entries) == ("glossaire", "Notes RAG maison", 1)
    entry = entries[0]
    assert entry.id == "notes-rag/late-chunking"
    assert (entry.domain, entry.aliases, entry.keywords) == ("RAG", ["chunking tardif"], ["embeddings", "contexte"])
    assert entry.url == "https://arxiv.org/abs/2005.11401"
    assert entry.body.startswith("Découper")

    info, entries = parse_markdown("# Mes idées\n\nSuivre les **SLM** sur Jetson.", "idees.md")
    assert (info.kind, info.title, entries[0].title) == ("note", "Mes idées", "Mes idées")
    assert entries[0].url == "#k=idees/mes-idees"


@pytest.mark.parametrize(("text", "error"), [
    ("---\ntype: recette\n---\n## A\ntexte", "type « recette » inconnu"),
    ("## Terme\nSource : pas-une-url\n\nTexte", "Entrée « Terme » invalide"),
    ("---\ntitle: vide\n---\n", "Aucun contenu"),
    ("---\ntitle: x\n## A\ntexte", "Front matter non fermé"),
])
def test_invalid_files_are_rejected(text, error):
    with pytest.raises(KnowledgeError, match=error):
        parse_markdown(text, "x.md")


def test_search_prefers_the_named_term_and_rules():
    base = load_knowledge()
    assert base.search("c'est quoi le KV cache ?")[0].title == "KV cache"
    assert base.search("définition de la quantification")[0].title == "Quantification"
    assert base.search("règles métiers pour les agents", kinds={"regles"})[0].domain == "Agents"
    assert base.search("zzzz introuvable") == []


def test_keyword_index_links_aliases_and_keywords():
    index = {item["term"].lower(): item for item in load_knowledge().keyword_index()}
    assert index["retrieval-augmented generation"]["entries"][0]["title"] == "RAG"
    assert index["rag"]["entries"][0]["role"] == "terme"
    assert any(ref["kind"] == "regles" for ref in index["agent"]["entries"] + index.get("agents", {"entries": []})["entries"])


def test_domain_detection_and_glossary_terms():
    base = load_knowledge()
    text = "New open-weights LLM release: FP8 quantization for vLLM inference serving on GPU, 70B parameters."
    assert base.detect_domains(text)[0] == "Inférence"
    found = {e.title for e in base.glossary_terms_in("Our agent uses MCP and RAG; drag the handle.")}
    assert {"MCP", "RAG", "Agent"} <= found
    assert "RAG" not in {e.title for e in base.glossary_terms_in("drag and drop, storage")}
    rules = base.rules_for(["Agents"])
    assert {domain for domain, _ in rules} == {"Transverse", "Agents"}


def test_upload_lifecycle(uploads):
    info = knowledge.save_upload("Notes RAG.md", NOTE)
    assert info.name == "notes-rag.md" and (uploads / "notes-rag.md").exists()
    base = load_knowledge()
    entry = base.get("notes-rag/late-chunking")
    assert entry.origin == "upload"
    assert base.search("late chunking")[0].id == entry.id

    with pytest.raises(KnowledgeExists):
        knowledge.save_upload("notes-rag.md", NOTE)
    knowledge.save_upload("notes-rag.md", NOTE.replace("après", "APRÈS"), replace=True)
    assert "APRÈS" in knowledge.read_file("notes-rag.md")["markdown"]

    knowledge.delete_upload("notes-rag.md")
    assert load_knowledge().get("notes-rag/late-chunking") is None


@pytest.mark.parametrize(("name", "content", "error"), [
    ("notes.txt", NOTE, "Markdown"),
    ("glossaire.md", NOTE, "fichier du dépôt"),
    ("big.md", "## A\n" + "x" * (knowledge.MAX_UPLOAD_BYTES + 1), "trop volumineux"),
    ("bad.md", "## Terme\nSource : ftp://x\n\nTexte", "invalide"),
])
def test_upload_validation_happens_before_writing(uploads, name, content, error):
    with pytest.raises(KnowledgeError, match=error):
        knowledge.save_upload(name, content)
    assert not uploads.exists() or not any(uploads.iterdir())


def test_uploads_are_confined_and_shipped_files_protected(uploads):
    assert knowledge.upload_name("../../etc/Passwd.md") == "passwd.md"
    for name in ["../x.md", "glossaire.md"]:
        with pytest.raises(KnowledgeError):
            knowledge.delete_upload(name)
    assert (settings.knowledge_dir / "glossaire.md").exists()
