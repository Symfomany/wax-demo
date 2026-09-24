import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from app import storage
from app.llm import StructuredLLM
from app.schemas import Document


NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def make_document(index: int, source: str = "rss", days_ago: int = 1, summary: str | None = None) -> dict:
    return {
        "source": source,
        "title": f"Release {source} {index}: quantization for local inference",
        "url": f"https://example.org/{source}/{index}",
        "published_at": (NOW - timedelta(days=days_ago)).isoformat(),
        "summary": summary
        or "A detailed technical note about FP8 quantization and vLLM serving on consumer GPUs.",
        "content": "",
        "tags": [source, f"feed-{source}"],
    }


def documents(*items: dict) -> list[Document]:
    return [Document.model_validate(item) for item in items]


def fake_ollama(
    prose=lambda call: "",
    relevance=lambda doc_id: 8,
    verdict=lambda signal_id: "keep" if signal_id % 2 else "drop",
    prompts: list | None = None,
):
    """Simule Ollama : répond selon le schéma demandé et les ids du prompt.

    - prose(n)       : texte ajouté aux résumés de l'Editor au n-ième appel (0, 1…)
    - relevance(id)  : pertinence renvoyée par le Scout pour chaque document
    - verdict(id)    : verdict du Critic pour chaque signal
    """
    editor_calls = {"n": 0}

    def invoke(messages, json_schema):
        prompt = messages[0]["content"]
        if prompts is not None:
            prompts.append((json_schema["title"], prompt))
        title = json_schema["title"]
        if title == "ScoutOutput":
            ids = [int(n) for n in re.findall(r"^\[(\d+)\]", prompt, re.MULTILINE)]
            picks = [
                {"doc_id": n, "relevance": relevance(n), "novelty": 6, "confidence": 7,
                 "why_it_matters": "Accélère l'inférence locale.", "tags": ["gpu"]}
                for n in ids
            ]
            return json.dumps({"picks": picks})
        ids = [int(n) for n in re.findall(r"signal_id=(\d+)", prompt)]
        if title == "CriticOutput":
            verdicts = [{"signal_id": n, "verdict": verdict(n), "rationale": "ok"} for n in ids]
            return json.dumps({"verdicts": verdicts})
        if title == "EditorOutput":
            extra = prose(editor_calls["n"])
            editor_calls["n"] += 1
            items = [
                {"signal_id": n, "summary": f"Résumé {n}. {extra}", "why_it_matters": "Impact GPU."}
                for n in ids
            ]
            return json.dumps({"executive_summary": "Semaine centrée sur l'inférence.", "items": items})
        raise AssertionError(title)

    return invoke


@pytest.fixture
def connection(tmp_path):
    connection = storage.connect(tmp_path / "watch.db")
    yield connection
    connection.close()


@pytest.fixture
def fake_llm(connection):
    return StructuredLLM(fake_ollama(), model="fake", connection=connection, max_calls=20)
