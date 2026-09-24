"""Grill-me : entretien par interruptions LangGraph, challenges, profil et mémoire."""

import json

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from app.grill import BRANCHES, COMMON, GrillContext, build_grill_graph, resume_payload
from app.llm import StructuredLLM
from app.memory import WatchMemory


def synthesis_llm(summary="Tu veux les modèles ouverts et les agents MCP.", fail=False, seen=None):
    def invoke(messages, schema):
        if seen is not None:
            seen.append(messages[0]["content"])
        if fail:
            return "pas du json"
        return json.dumps({"summary": summary, "extra_keywords": ["small models", "tool use", "quant", "llm", "ai"]})

    return StructuredLLM(invoke, model="fake")


def start(llm=None, feeds=frozenset()):
    store = InMemoryStore()
    graph = build_grill_graph(GrillContext(llm=llm, configured_feeds=set(feeds)), InMemorySaver(), store)
    config = {"configurable": {"thread_id": "g1"}}
    first = graph.invoke({"queue": ["domains"]}, config)["__interrupt__"][0].value
    return graph, config, store, first


def answer(graph, config, **reply):
    result = graph.invoke(Command(resume=resume_payload(reply)), config)
    if "__interrupt__" in result:
        return result["__interrupt__"][0].value, None
    return None, result["profile"]


def test_first_question_offers_domains_with_a_recommendation():
    _, _, _, question = start()
    assert question["id"] == "domains" and question["multi"]
    assert {"llm", "robotics", "events", "models", "architecture"} <= {o["id"] for o in question["options"]}
    assert question["recommended"] and question["why"]


def test_interview_walks_each_chosen_branch_then_common_questions():
    graph, config, _, _ = start()
    question, _ = answer(graph, config, options=["robotics", "events"])
    asked = [question["id"]]
    while question:
        question, profile = answer(graph, config, recommended=True)
        if question:
            asked.append(question["id"])

    expected = [q.id for q in BRANCHES["robotics"] + BRANCHES["events"] + COMMON]
    assert asked == expected
    assert profile["domains"] == ["robotics", "events"]
    assert {"vision-language-action", "ros", "neurips"} <= set(profile["keywords"])


def test_too_many_domains_are_challenged():
    graph, config, _, _ = start()
    question, _ = answer(graph, config, options=["llm", "agents", "robotics", "tools"])
    assert question["id"] == "priorities" and "4 domaines" in question["text"]
    assert [o["id"] for o in question["options"]] == ["llm", "robotics", "agents", "tools"]

    question, _ = answer(graph, config, options=["llm", "agents", "robotics", "tools"])  # borné à 3
    assert graph.get_state(config).values["answers"][-1]["options"] == ["llm", "agents", "robotics"]


def test_vague_answer_triggers_a_clarification():
    graph, config, _, _ = start()
    question, _ = answer(graph, config, options=["tools"])
    question, _ = answer(graph, config, text="tout")
    assert question["id"] == "clarify:tools_kind" and "exemple concret" in question["text"]
    question, _ = answer(graph, config, text="LangGraph 1.2, Ollama")
    assert question["id"] == "exclusions"


def run_to_end(graph, config, keywords="vLLM, Jetson Thor"):
    question, profile = answer(graph, config, options=["llm", "robotics"])
    while question:
        reply = {"text": keywords} if question["id"] == "keywords" else {"recommended": True}
        question, profile = answer(graph, config, **reply)
    return profile


def test_profile_is_saved_and_injected_in_prompts():
    graph, config, store, _ = start(llm=synthesis_llm())

    profile = run_to_end(graph, config)

    assert profile["summary"] == "Tu veux les modèles ouverts et les agents MCP."
    assert {"open weights", "vLLM", "Jetson Thor", "small models"} <= set(profile["keywords"])
    assert {"tutorial", "funding"} <= set(profile["exclusions"])
    assert profile["depth"] == "summary" and profile["frequency"] == "daily"
    memory = WatchMemory(store)
    assert memory.interests()["keywords"] == profile["keywords"]
    context = memory.prompt_context()
    assert "Centres d'intérêt déclarés (Grill-me) : Tu veux les modèles ouverts" in context
    assert "vLLM" in context and "À écarter : tutorial" in context


def test_suggested_sources_skip_configured_feeds_and_never_invent_event_urls():
    graph, config, _, _ = start(feeds={"https://rss.arxiv.org/rss/cs.CL"})
    question, profile = answer(graph, config, options=["llm", "robotics", "events"])
    while question:
        question, profile = answer(graph, config, recommended=True)

    urls = [s["url"] for s in profile["suggested_sources"]]
    assert "https://rss.arxiv.org/rss/cs.RO" in urls
    assert "https://rss.arxiv.org/rss/cs.CL" not in urls  # déjà configuré
    events = next(s for s in profile["suggested_sources"] if "événements" in s["name"])
    assert events["url"] == "" and "ajout-source" in events["reason"]


def test_llm_failure_falls_back_to_deterministic_summary():
    graph, config, _, _ = start(llm=synthesis_llm(fail=True))
    profile = run_to_end(graph, config)
    assert profile["summary"].startswith("Priorités : LLM & modèles de langage, Robotique")


def test_skipping_everything_still_yields_a_profile():
    graph, config, _, _ = start()
    question, profile = answer(graph, config)  # aucun domaine : recommandation appliquée
    while question:
        question, profile = answer(graph, config)
    assert profile["domains"] == ["llm", "agents", "architecture"]


def test_synthesis_sees_labels_and_ignores_option_ids_as_keywords():
    seen = []
    graph, config, _, _ = start(llm=synthesis_llm(seen=seen))
    profile = run_to_end(graph, config)

    assert "LLM & modèles de langage" in seen[0] and "→ llm, robotics" not in seen[0]
    assert {"small models", "tool use"} <= set(profile["keywords"])
    assert not {"quant", "ai"} & set(profile["keywords"])  # identifiant d'option, trop court
