"""Assistant de veille : routage, outils, streaming, citations, guard, mémoire de conversation."""

import json

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from app import storage
from app.chat.agent import ChatContext, build_chat_graph, guard_answer, rule_route, stream_chat
from app.chat.tools import ChatServices
from app.llm import StructuredLLM
from app.memory import WatchMemory
from tests.conftest import documents, make_document


def router(tool: str = "search_watch", query: str = "quantization", calls: list | None = None):
    def invoke(messages, schema):
        if calls is not None:
            calls.append(messages[0]["content"])
        return json.dumps({"tool": tool, "query": query})

    return StructuredLLM(invoke, model="fake-router")


def chat_model(*answers: str):
    return GenericFakeChatModel(messages=iter([AIMessage(answer) for answer in answers]))


@pytest.fixture
def services(connection, tmp_path):
    storage.save_documents(connection, documents(
        make_document(1, "rss", summary="FP8 quantization lands in vLLM for Ampere GPUs."),
        make_document(2, "arxiv", summary="A benchmark for agentic retrieval."),
    ))
    store = InMemoryStore()
    WatchMemory(store).add_lesson("Écarter les tutoriels", approved=False, run_id="r0")
    return ChatServices(connection=connection, store=store, reports_dir=tmp_path / "reports")


def make_chat(services, router_llm=None, model=None):
    context = ChatContext(services=services, router_llm=router_llm or router(), chat_model=model or chat_model("—"))
    return build_chat_graph(context, InMemorySaver())


def run(graph, message, conversation="c1"):
    events = list(stream_chat(graph, conversation, message, {}))
    return events, events[-1]


@pytest.mark.parametrize(("message", "tool"), [
    ("Lance une veille maintenant", "run_watch"),
    ("peux-tu relancer la veille ?", "run_watch"),
    ("Mets à jour la page Notion", "notion_sync"),
    ("Trouve des nouveaux repos GitHub sur les agents MCP", "github_search"),
    ("Qu'as-tu en mémoire ?", "memory_status"),
    ("Montre les rapports", "list_reports"),
    ("Quoi de neuf cette semaine ?", "latest_digests"),
    ("Bonjour !", "none"),
    ("Grill me sur ma veille", "grill_me"),
    ("Interroge-moi pour préciser ce que je cherche", "grill_me"),
])
def test_rules_route_explicit_intents(message, tool):
    assert rule_route(message).tool == tool


def test_rules_leave_open_questions_to_the_llm_router():
    assert rule_route("Que sait-on sur la quantization FP8 ?") is None
    assert rule_route("Trouve des nouveaux repos GitHub sur les agents MCP").query == "agents MCP"


def test_search_answer_streams_tokens_and_cites_sources(services):
    graph = make_chat(services, model=chat_model("vLLM gère désormais FP8 sur Ampere [1]."))

    events, final = run(graph, "Que sait-on sur la quantization FP8 ?")

    types = [event["type"] for event in events]
    assert types[0] == "tool_start" and types[1] == "tool_end"
    assert "token" in types and types[-1] == "final"
    assert "".join(e["text"] for e in events if e["type"] == "token") == final["text"]
    assert final["tool"] == "search_watch"
    assert final["sources"][0]["url"] == "https://example.org/rss/1"
    assert final["text"] == "vLLM gère désormais FP8 sur Ampere [1]."
    assert final["warnings"] == []


def test_guard_removes_invented_urls_and_out_of_range_citations(services):
    invented = "Voir https://invented.example/bench [1] et aussi [7]."
    graph = make_chat(services, model=chat_model(invented))

    _, final = run(graph, "Que sait-on sur la quantization FP8 ?")

    assert "invented.example" not in final["text"] and "[lien retiré]" in final["text"]
    assert "[7]" not in final["text"] and "[1]" in final["text"]
    assert len(final["warnings"]) == 2
    # l'historique conserve la version contrôlée, avec ses sources
    stored = graph.get_state({"configurable": {"thread_id": "chat-c1"}}).values["messages"][-1]
    assert "invented.example" not in stored.content
    assert stored.additional_kwargs["sources"][0]["url"] == "https://example.org/rss/1"


def test_guard_keeps_sourced_urls():
    text, warnings = guard_answer("Source : https://example.org/a.", [{"url": "https://example.org/a"}])
    assert text == "Source : https://example.org/a." and warnings == []


def test_run_watch_is_a_deterministic_action(services):
    started = []
    services.start_run = lambda options: started.append(options) or "run-1234567890"
    graph = make_chat(services, model=chat_model())  # aucun appel au modèle attendu

    _, final = run(graph, "Lance une veille")

    assert started == [{"collect": True}]
    assert final["data"] == {"run_id": "run-1234567890", "keywords": []}
    assert final["text"].startswith("Veille lancée.")


def test_busy_run_is_reported(services):
    def busy(options):
        raise RuntimeError("Une veille est déjà en cours.")

    services.start_run = busy
    _, final = run(make_chat(services), "lance la veille")
    assert final["text"] == "Une veille est déjà en cours."


def test_notion_not_configured_explains_setup(services):
    _, final = run(make_chat(services), "publie dans Notion")
    assert "NOTION_TOKEN" in final["text"]


def test_notion_sync_answer_links_the_page(services):
    services.notion_sync = lambda connection: {"url": "https://www.notion.so/p1", "digests": 4}
    _, final = run(make_chat(services), "mets à jour notion")
    assert final["sources"][0]["url"] == "https://www.notion.so/p1"
    assert "4 dernières veilles [1]" in final["text"]


def test_latest_digests_without_publication(services):
    _, final = run(make_chat(services), "quoi de neuf ?")
    assert "Aucune veille n'a encore été publiée" in final["text"]


def test_memory_lessons_are_injected_in_system_prompt(services):
    seen = []

    class SpyModel(GenericFakeChatModel):
        def _generate(self, messages, *args, **kwargs):
            seen.append(messages[0].content)
            return super()._generate(messages, *args, **kwargs)

    graph = make_chat(services, model=SpyModel(messages=iter([AIMessage("ok")])))
    run(graph, "Que sait-on sur la quantization FP8 ?")

    assert "Écarter les tutoriels" in seen[0]
    assert "[1] Release rss 1" in seen[0]


def test_conversation_history_is_kept_per_thread(services):
    calls = []
    graph = make_chat(services, router_llm=router(calls=calls), model=chat_model("r1", "r2", "r3"))

    run(graph, "Parle-moi de FP8", conversation="a")
    run(graph, "Et côté benchmarks ?", conversation="a")
    run(graph, "Autre sujet", conversation="b")

    assert "human: Parle-moi de FP8" in calls[1]
    assert "Parle-moi de FP8" not in calls[2]
    state = graph.get_state({"configurable": {"thread_id": "chat-a"}}).values
    assert [m.type for m in state["messages"]] == ["human", "ai", "human", "ai"]


def test_router_failure_falls_back_to_search(services):
    broken = StructuredLLM(lambda messages, schema: "pas du json", model="broken")
    _, final = run(make_chat(services, router_llm=broken, model=chat_model("ok")), "FP8 ?")
    assert final["tool"] == "search_watch"


def test_tool_failure_becomes_an_answer(services):
    def failing(query):
        raise ConnectionError("MCP injoignable")

    services.github_search = failing
    _, final = run(make_chat(services), "nouveaux repos github agents")
    assert "a échoué" in final["text"] and "MCP injoignable" in final["text"]


def test_chat_launches_a_targeted_watch(services):
    started = []
    services.start_run = lambda options: started.append(options) or "run-42"

    _, final = run(make_chat(services, model=chat_model()), "Relance la veille sur MCP, agents et RAG")

    assert started == [{"collect": True, "keywords": ["MCP", "agents", "RAG"]}]
    assert "ciblée sur **MCP, agents, RAG**" in final["text"]


def test_quoted_passages_do_not_drive_routing(services):
    message = "> Nouveaux repos GitHub pour Notion\nQue sait-on sur la quantization ?"
    assert rule_route(message) is None  # la citation ne déclenche ni GitHub ni Notion


def test_final_event_reports_path_and_engagement(services):
    graph = make_chat(services, model=chat_model("Réponse [1]."))

    _, final = run(graph, "Que sait-on sur la quantization FP8 ?")

    assert [step["node"] for step in final["steps"]] == ["route", "act", "respond", "guard"]
    assert final["steps"][0]["detail"].startswith("LLM (router.md) → search_watch")
    engaged = final["engaged"]
    assert engaged["tool"] == "search_watch" and engaged["routed_by"] == "llm"
    assert engaged["prompts"] == ["router.md", "chat-system.md.j2"]
    assert engaged["data"] == ["SQLite FTS5 (documents)"]


def test_action_engagement_lists_agents_skills_and_mcp(services):
    services.start_run = lambda options: "run-1"
    _, final = run(make_chat(services, model=chat_model()), "lance une veille")
    engaged = final["engaged"]
    assert engaged["agents"][:2] == ["supervisor", "collector"] and engaged["skills"] == ["veille-tech"]
    assert engaged["mcp"] == ["github-scout"] and engaged["prompts"] == []


def test_guard_normalizes_citation_lists():
    sources = [{"url": "https://a.example/1"}, {"url": "https://a.example/2"}]
    text, warnings = guard_answer("Vu dans [1, 2] et [2, 9].", sources)
    assert text == "Vu dans [1][2] et [2]."
    assert warnings == ["Citation [9] sans source retirée"]


# --- Base de connaissances et challenge d'une review ----------------------------------------


@pytest.mark.parametrize("message", [
    "C'est quoi le KV cache ?",
    "Qu'est-ce que le RAG ?",
    "Définition de la quantification",
    "Quelles sont les règles métiers pour les agents ?",
])
def test_knowledge_questions_are_routed_to_the_knowledge_base(message):
    assert rule_route(message).tool == "knowledge_search"


def test_knowledge_answer_cites_glossary_entries(services):
    seen = []

    class SpyModel(GenericFakeChatModel):
        def _generate(self, messages, *args, **kwargs):
            seen.append(messages[0].content)
            return super()._generate(messages, *args, **kwargs)

    graph = make_chat(services, model=SpyModel(messages=iter([AIMessage("Le KV cache mémorise les clés [1].")])))
    _, final = run(graph, "C'est quoi le KV cache ?")

    assert final["tool"] == "knowledge_search"
    assert final["sources"][0]["title"] == "KV cache"
    assert final["sources"][0]["url"] == "#k=glossaire/kv-cache"  # entrée sans source externe
    assert "[1] KV cache (glossaire, Inférence)" in seen[0]
    assert final["engaged"]["data"] == ["knowledge/ (glossaire, règles métiers, notes)"]


def test_review_conversation_challenges_the_review(services):
    from app.review import ReviewContext, build_review_graph, extract_page
    from tests.conftest import ARTICLE_HTML, ARTICLE_URL, fake_review_llm

    context = ReviewContext(llm=StructuredLLM(fake_review_llm(), model="fake-review"), connection=services.connection,
                            fetch=lambda url: extract_page(ARTICLE_HTML, url))
    record = build_review_graph(context).invoke({"url": ARTICLE_URL})["record"]
    seen = []

    class SpyModel(GenericFakeChatModel):
        def _generate(self, messages, *args, **kwargs):
            seen.append(messages[0].content)
            return super()._generate(messages, *args, **kwargs)

    graph = make_chat(services, model=SpyModel(messages=iter([
        AIMessage("L'article parle d'un benchmark interne [1]."), AIMessage("Oui [1]."),
    ])))
    events = list(stream_chat(graph, record["conversation_id"], "Le gain de 1.8x est-il mesuré avec un protocole ?",
                              {}, review_id=record["id"]))
    final = events[-1]

    assert final["tool"] == "challenge_review"
    assert final["sources"][0]["url"] == ARTICLE_URL
    assert "MODE CHALLENGE" in seen[0] and "batch size 64" in seen[0]
    assert "NON étayée" in seen[0]  # la review transmise signale l'affirmation non étayée
    # Le fil reste en mode challenge sans renvoyer review_id (état conservé par le checkpointer).
    _, second = run(graph, "Et la date ?", conversation=record["conversation_id"])
    assert second["tool"] == "challenge_review"
