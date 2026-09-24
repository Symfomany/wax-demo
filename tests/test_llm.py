import json

import pytest

from app.llm import BudgetExceeded, LLMOutputError, StructuredLLM, extract_json, inline_refs
from app.schemas import ScoutOutput


VALID = json.dumps(
    {"picks": [{"doc_id": 1, "relevance": 9, "novelty": 5, "confidence": 8, "why_it_matters": "x"}]}
)


def scripted(*responses):
    calls = []

    def invoke(messages, json_schema):
        calls.append(messages)
        return responses[len(calls) - 1]

    return invoke, calls


def test_retries_with_validation_error_feedback():
    invoke, calls = scripted('{"picks": [{"doc_id": 1, "relevance": 42}]}', VALID)
    llm = StructuredLLM(invoke, model="fake")

    result = llm.generate("prompt", ScoutOutput)

    assert result.picks[0].relevance == 9
    assert len(calls) == 2
    assert "ne respecte pas le schéma" in calls[1][-1]["content"]


def test_raises_after_max_attempts():
    invoke, _ = scripted("pas du json", "toujours pas")
    with pytest.raises(LLMOutputError):
        StructuredLLM(invoke, model="fake").generate("prompt", ScoutOutput)


def test_cache_avoids_second_call(connection):
    invoke, calls = scripted(VALID)
    llm = StructuredLLM(invoke, model="fake", connection=connection)

    llm.generate("prompt", ScoutOutput)
    llm.generate("prompt", ScoutOutput)

    assert len(calls) == 1
    assert llm.cache_hits == 1


def test_budget_is_enforced():
    invoke, _ = scripted(VALID, VALID)
    llm = StructuredLLM(invoke, model="fake", max_calls=1)
    llm.generate("a", ScoutOutput)
    with pytest.raises(BudgetExceeded):
        llm.generate("b", ScoutOutput)


def test_extract_json_strips_markdown_fences():
    assert json.loads(extract_json('Voici :\n```json\n{"a": 1}\n```')) == {"a": 1}


def test_inline_refs_removes_references():
    schema = inline_refs(ScoutOutput.model_json_schema())
    assert "$ref" not in json.dumps(schema)
    assert schema["properties"]["picks"]["items"]["properties"]["doc_id"]["type"] == "integer"
