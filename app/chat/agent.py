"""Assistant de veille conversationnel : route → act → respond → guard.

- route   : règles déterministes d'abord (actions explicites), puis LLM en sortie
            structurée (fonctionne avec des modèles sans « tool calling » natif).
- act     : exécute un outil LangChain et publie des événements de progression.
- respond : réponse en streaming, fondée uniquement sur le résultat de l'outil,
            ou réponse déterministe pour les actions (lancer, publier…).
- guard   : retire les URLs absentes des sources et les citations hors plage.

La conversation est conservée par le checkpointer (thread = conversation).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Annotated, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.chat.tools import ChatServices, build_tools
from app.harness.hooks import URL_PATTERN
from app.harness.prompts import render_prompt
from app.instructions import render_instructions
from app.llm import BudgetExceeded, LLMOutputError, StructuredLLM
from app.memory import WatchMemory

RouterTool = Literal[
    "search_watch", "latest_digests", "run_watch", "github_search",
    "memory_status", "notion_sync", "list_reports", "grill_me", "knowledge_search", "none",
]
# `challenge_review` n'est jamais choisi par le routeur : il sert le chat d'une review.
ToolName = Literal[RouterTool, "challenge_review"]


class ChatDecision(BaseModel):
    tool: RouterTool
    query: str = Field("", description="Mots-clés si l'outil en a besoin, sinon vide")


# Règles déterministes : une intention explicite ne dépend pas du LLM.
RULES: list[tuple[re.Pattern, ToolName]] = [
    (re.compile(r"\b(grill[- ]?me|grille[- ]moi|interroge[- ]moi|questionne[- ]moi|pose[- ]moi des questions|cerne mes (besoins|centres d'intérêt))\b", re.I), "grill_me"),
    (re.compile(r"\b(lance|lancer|relance|relancer|démarre|démarrer|exécute|exécuter)\b.{0,40}\bveille\b", re.I), "run_watch"),
    (re.compile(r"\bnotion\b", re.I), "notion_sync"),
    (re.compile(r"\b(github|repos?|dépôts?|depots?)\b", re.I), "github_search"),
    (re.compile(r"\b(mémoire|memoire|leçons?|préférences?|santé des sources)\b", re.I), "memory_status"),
    (re.compile(r"\brapports?\b", re.I), "list_reports"),
    (re.compile(r"\b(quoi de neuf|dernières? veilles?|derniers? digests?|résume la veille|nouveautés)\b", re.I), "latest_digests"),
    (re.compile(r"\b(d[ée]finitions?|d[ée]finir|d[ée]finis|glossaire|que signifie|signifie|r[èe]gles? m[ée]tiers?|"
                r"base de connaissances?|knowledge)\b|c['’]est quoi|qu['’]est[- ]ce (?:que|qu['’])\s*(?:c['’]est|un|une|le|la|les|l['’])",
                re.I), "knowledge_search"),
    (re.compile(r"^\s*(bonjour|salut|hello|merci|coucou)\b[\s!.?]*$", re.I), "none"),
]
QUERY_NOISE = re.compile(
    r"\b(trouve|cherche|montre|donne|moi|les|des|de|du|la|le|nouveaux?|nouvelles?|récents?|"
    r"github|repos?|dépôts?|sur|pour|qui|quels?|quelles?|parle|parlent|y a-t-il|a-t-il|il)\b",
    re.I,
)


FOCUS = re.compile(r"\b(?:sur|autour de|avec les mots[- ]clés?|mots[- ]clés?\s*:?)\s+(.+)$", re.I)


def intent_text(message: str) -> str:
    """Le message sans les passages cités (« > … ») : ils ne doivent pas influencer le routage."""
    return "\n".join(line for line in message.splitlines() if not line.lstrip().startswith(">")).strip()


def rule_route(message: str) -> ChatDecision | None:
    message = intent_text(message)
    for pattern, tool in RULES:
        if pattern.search(message):
            query = message
            if tool == "github_search":
                query = re.sub(r"\s+", " ", QUERY_NOISE.sub(" ", message)).strip(" ?!.") or "llm"
            elif tool == "run_watch":
                # « relance la veille sur MCP, agents » → veille ciblée sur ces mots-clés
                focus = FOCUS.search(message)
                query = focus.group(1).strip(" ?!.") if focus else ""
            return ChatDecision(tool=tool, query=query)
    return None


# Ce que chaque outil engage dans le harness (affiché sous chaque réponse).
TOOL_ENGAGEMENT: dict[str, dict[str, list[str]]] = {
    "search_watch": {"data": ["SQLite FTS5 (documents)"]},
    "latest_digests": {"data": ["digests publiés"]},
    "run_watch": {"agents": ["supervisor", "collector", "scout", "critic", "fact-checker", "editor"],
                  "skills": ["veille-tech"], "mcp": ["github-scout"]},
    "github_search": {"skills": ["github-scout"], "mcp": ["github-scout"]},
    "memory_status": {"data": ["Store LangGraph (mémoire)"]},
    "notion_sync": {"skills": ["rapport-veille"], "mcp": ["notion-veille"]},
    "list_reports": {"data": ["reports/"]},
    "grill_me": {"skills": ["grill-me"], "data": ["Store LangGraph (centres d'intérêt)"]},
    "knowledge_search": {"data": ["knowledge/ (glossaire, règles métiers, notes)"]},
    "challenge_review": {"agents": ["reviewer"], "skills": ["review-actu"],
                         "data": ["reviews (SQLite)", "knowledge/"]},
    "none": {},
}


def engagement(decision: dict, result: dict, visited: list[str]) -> dict:
    tool = decision["tool"]
    spec = TOOL_ENGAGEMENT.get(tool, {})
    prompts = []
    if decision.get("routed_by") == "llm":
        prompts.append("router.md")
    if not result.get("direct"):
        prompts.append("chat-system.md.j2")
    return {
        "nodes": visited,
        "tool": None if tool == "none" else tool,
        "routed_by": decision.get("routed_by", "rule"),
        "agents": spec.get("agents", []),
        "skills": spec.get("skills", []),
        "mcp": spec.get("mcp", []),
        "data": spec.get("data", []),
        "prompts": prompts,
    }


class ChatState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    decision: dict
    result: dict
    answer: str
    warnings: list[str]
    review_id: str  # conversation de challenge d'une review (conservé par le checkpointer)


@dataclass
class ChatContext:
    services: ChatServices
    router_llm: StructuredLLM
    chat_model: BaseChatModel
    history_turns: int = 6
    tools: dict = field(default_factory=dict)


def guard_answer(answer: str, sources: list[dict]) -> tuple[str, list[str]]:
    """Retire les URLs non sourcées et les citations [n] hors plage."""
    allowed = {source["url"] for source in sources}
    warnings = []

    def strip_url(match: re.Match) -> str:
        url = match.group(0).rstrip(".,;)")
        if url in allowed:
            return match.group(0)
        warnings.append(f"URL non sourcée retirée : {url}")
        return "[lien retiré]"

    answer = URL_PATTERN.sub(strip_url, answer)

    def check_citation(match: re.Match) -> str:
        # [3] ou liste [1, 2] : chaque numéro doit correspondre à une source
        numbers = [int(n) for n in re.findall(r"\d+", match.group(1))]
        kept = [n for n in numbers if 1 <= n <= len(sources)]
        for number in numbers:
            if number not in kept:
                warnings.append(f"Citation [{number}] sans source retirée")
        return "".join(f"[{n}]" for n in kept)

    answer = re.sub(r"\[(\d+(?:\s*,\s*\d+)*)\]", check_citation, answer)
    return answer.strip(), warnings


def build_chat_graph(context: ChatContext, checkpointer=None):
    context.tools = context.tools or build_tools(context.services)

    def history(state: ChatState) -> list[AnyMessage]:
        return state["messages"][-(context.history_turns * 2):]

    def route(state: ChatState) -> dict:
        message = state["messages"][-1].content
        if state.get("review_id"):
            # Message complet : les passages cités (« > … ») orientent le choix des extraits.
            return {"decision": {"tool": "challenge_review", "query": message,
                                 "review_id": state["review_id"], "routed_by": "rule"}}
        decision = rule_route(message)
        routed_by = "rule" if decision else "llm"
        if decision is None:
            transcript = "\n".join(f"{m.type}: {str(m.content)[:200]}" for m in history(state)[:-1]) or "—"
            try:
                decision = context.router_llm.generate(
                    render_prompt("router", history=transcript, message=message), ChatDecision
                )
            except (LLMOutputError, BudgetExceeded):
                decision = ChatDecision(tool="search_watch", query=message)
            if decision.tool in {"search_watch", "github_search"} and not decision.query.strip():
                decision.query = message
        return {"decision": decision.model_dump() | {"routed_by": routed_by}}

    def act(state: ChatState, config) -> dict:
        decision = state["decision"]
        writer = get_stream_writer()
        if decision["tool"] == "none":
            return {"result": {"summary": "", "sources": [], "context": "", "direct": None, "data": {}}}
        tool = context.tools[decision["tool"]]
        args = {}
        if "query" in tool.args:
            args["query"] = decision["query"]
        if "keywords" in tool.args and decision["query"]:
            args["keywords"] = decision["query"]
        if "review_id" in tool.args:
            args["review_id"] = decision.get("review_id", "")
        writer({"type": "tool_start", "tool": decision["tool"], "args": args})
        try:
            result = tool.invoke(args, config)
        except Exception as error:  # noqa: BLE001 — l'échec d'un outil devient une réponse
            result = {"summary": "échec", "sources": [], "context": "", "data": {},
                      "direct": f"L'outil `{decision['tool']}` a échoué : {error}"}
        writer({"type": "tool_end", "tool": decision["tool"], "summary": result["summary"],
                "sources": result["sources"], "data": result["data"]})
        return {"result": result}

    def respond(state: ChatState, config) -> dict:
        result = state["result"]
        if result.get("direct"):
            return {"answer": result["direct"], "messages": [AIMessage(result["direct"])]}
        system = render_instructions(
            "chat",
            memory=WatchMemory(context.services.store).prompt_context(),
            tool=state["decision"]["tool"],
            context=result.get("context") or "Aucun (conversation générale).",
        )
        response = context.chat_model.invoke([SystemMessage(system), *history(state)], config)
        # Le message renvoyé garde l'id des tokens streamés : pas de doublon côté interface.
        return {"answer": response.text, "messages": [response]}

    def guard(state: ChatState) -> dict:
        answer, warnings = guard_answer(state["answer"], state["result"].get("sources", []))
        if not answer:
            answer = "Je n'ai pas de réponse sourcée à proposer."
        # Remplace (même id) la réponse brute dans l'historique par la version
        # contrôlée : une URL retirée ne doit pas revenir au tour suivant.
        stored = AIMessage(
            answer,
            id=state["messages"][-1].id,
            additional_kwargs={"sources": state["result"].get("sources", []),
                               "tool": state["decision"]["tool"]},
        )
        return {"answer": answer, "warnings": warnings, "messages": [stored]}

    builder = StateGraph(ChatState)
    builder.add_node("route", route)
    builder.add_node("act", act)
    builder.add_node("respond", respond)
    builder.add_node("guard", guard)
    builder.add_edge(START, "route")
    builder.add_edge("route", "act")
    builder.add_edge("act", "respond")
    builder.add_edge("respond", "guard")
    builder.add_edge("guard", END)
    return builder.compile(checkpointer=checkpointer)


def stream_chat(graph, conversation_id: str, message: str, config: dict, review_id: str | None = None):
    """Événements pour l'interface : tool_start/tool_end, token, final (+ parcours du graphe).

    `review_id` ouvre une conversation de challenge : il reste dans l'état du fil.
    """
    config = config | {"configurable": {"thread_id": f"chat-{conversation_id}"}}
    steps, started = [], time.perf_counter()
    last = started
    inputs = {"messages": [HumanMessage(message)], **({"review_id": review_id} if review_id else {})}
    for mode, chunk in graph.stream(
        inputs, config, stream_mode=["messages", "custom", "values", "updates"]
    ):
        if mode == "updates":
            now = time.perf_counter()
            for node, update in chunk.items():
                steps.append({"graph": "chat", "node": node, "ms": round((now - last) * 1000),
                              "detail": step_detail(node, update or {})})
            last = now
        elif mode == "custom":
            yield chunk
        elif mode == "messages":
            token, metadata = chunk
            text = getattr(token, "text", "")  # .text : ignore les blocs « thinking » (Claude)
            if metadata.get("langgraph_node") == "respond" and text:
                yield {"type": "token", "text": text}
        elif mode == "values":
            final = chunk  # le dernier état du tour (après guard)
    yield {
        "type": "final",
        "text": final["answer"],
        "tool": final["decision"]["tool"],
        "sources": final["result"].get("sources", []),
        "data": final["result"].get("data", {}),
        "warnings": final["warnings"],
        "steps": steps,
        "total_ms": round((time.perf_counter() - started) * 1000),
        "engaged": engagement(final["decision"], final["result"], [s["node"] for s in steps]),
    }


def step_detail(node: str, update: dict) -> str:
    if node == "route" and "decision" in update:
        decision = update["decision"]
        how = "règle" if decision.get("routed_by") == "rule" else "LLM (router.md)"
        return f"{how} → {decision['tool']}" + (f" « {decision['query'][:60]} »" if decision.get("query") else "")
    if node == "act" and "result" in update:
        return update["result"].get("summary", "")
    if node == "respond":
        return f"{len(update.get('answer', ''))} caractères"
    if node == "guard":
        return f"{len(update.get('warnings', []))} correction(s)"
    return ""
