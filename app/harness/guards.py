"""Guards LangChain / LangGraph / MCP.

Trois points d'ancrage, du plus proche de l'extérieur au plus proche de la sortie :

1. MCP  — `mcp_guard` : intercepteur langchain-mcp-adapters (liste blanche
   d'outils en lecture seule, bornage des arguments, journal des appels).
2. LLM  — `sanitize_untrusted` : neutralise les injections de prompt présentes
   dans les contenus collectés avant qu'ils n'entrent dans un prompt.
3. Graphe — `node_contract` : un nœud LangGraph ne peut écrire que les clés
   déclarées, validées par un modèle Pydantic (contrat d'état).
   Les garde-fous de publication restent dans app/harness/hooks.py.
"""

import functools
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError


# --- 1. MCP ---------------------------------------------------------------

READ_ONLY_TOOLS = {"search_repositories", "list_releases", "get_readme"}
MAX_PER_PAGE = 10


class GuardViolation(RuntimeError):
    pass


def clamp_tool_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name not in READ_ONLY_TOOLS:
        raise GuardViolation(f"Outil MCP non autorisé : {name}")
    clamped = dict(args)
    if "perPage" in clamped:
        clamped["perPage"] = max(1, min(int(clamped["perPage"]), MAX_PER_PAGE))
    if len(str(clamped.get("query", ""))) > 256:
        raise GuardViolation("Requête MCP trop longue")
    return clamped


NOTION_TOOLS = {"create_page", "append_blocks", "archive_page", "search_pages"}


def _notion_id(value: str) -> str:
    return str(value).replace("-", "").lower()


def notion_policy(parent_page_id: str, owned_pages: set[str]) -> Callable:
    """Écriture Notion bornée : uniquement sous la page parente configurée,
    et seules les pages créées par la veille peuvent être complétées ou archivées."""

    def check(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in NOTION_TOOLS:
            raise GuardViolation(f"Outil MCP non autorisé : {name}")
        if name == "create_page" and _notion_id(args.get("parent_page_id", "")) != _notion_id(parent_page_id):
            raise GuardViolation("Création Notion hors de la page parente configurée")
        target = args.get("page_id") or args.get("block_id")
        if name in {"archive_page", "append_blocks"} and _notion_id(target) not in {
            _notion_id(page) for page in owned_pages
        }:
            raise GuardViolation(f"Page Notion non gérée par la veille : {target}")
        return args

    return check


def _audit_repr(args: dict[str, Any]) -> dict[str, Any]:
    """Journal lisible : les listes (blocs Notion…) sont résumées par leur taille."""
    return {key: f"<{len(value)} éléments>" if isinstance(value, list) else value for key, value in args.items()}


def make_mcp_guard(audit_log: list[str] | None = None, policy: Callable = clamp_tool_args):
    """Intercepteur ToolCallInterceptor pour MultiServerMCPClient."""

    async def mcp_guard(request, handler):
        args = policy(request.name, request.args)
        if audit_log is not None:
            audit_log.append(f"{request.server_name}.{request.name}({_audit_repr(args)})")
        return await handler(request.override(args=args))

    return mcp_guard


# --- 2. Contenus non fiables -------------------------------------------------

INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"ignore\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above)\s+instructions?",
        r"disregard\s+(all\s+|the\s+)?(previous|prior|above)",
        r"oublie\s+(toutes\s+)?(les\s+)?instructions",
        r"you\s+are\s+now\s+",
        r"^\s*(system|assistant)\s*:",
        r"</?(system|instructions?|prompt)>",
    ]
]


def sanitize_untrusted(text: str, limit: int | None = None) -> str:
    """Neutralise les consignes injectées dans un document collecté."""
    cleaned = text
    for pattern in INJECTION_PATTERNS:
        cleaned = pattern.sub("[consigne neutralisée]", cleaned)
    cleaned = cleaned.replace("```", "'''")
    return cleaned[:limit] if limit else cleaned


# --- 3. Contrat d'état des nœuds ---------------------------------------------


def node_contract(output_model: type[BaseModel]) -> Callable:
    """Valide la mise à jour d'état renvoyée par un nœud LangGraph.

    Le modèle doit interdire les champs inconnus (extra="forbid") : un nœud qui
    écrit une clé non prévue échoue immédiatement au lieu de corrompre l'état.
    """

    def decorator(node: Callable) -> Callable:
        @functools.wraps(node)
        def wrapper(*args, **kwargs):
            update = node(*args, **kwargs)
            if isinstance(update, dict):
                try:
                    output_model.model_validate(update)
                except ValidationError as error:
                    raise GuardViolation(
                        f"Contrat d'état violé par {node.__name__} : {error}"
                    ) from error
            return update

        return wrapper

    return decorator
