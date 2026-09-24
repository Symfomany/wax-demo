"""Outils communs aux clients MCP (stdio) de l'application."""

import asyncio
import json
import sys
from pathlib import Path

SERVERS_DIR = Path(__file__).resolve().parent / "mcp_servers"


def python_stdio(script: str, env: dict[str, str | None]) -> dict:
    """Connexion stdio vers un serveur MCP Python du projet.

    Seules les variables fournies (non vides) sont transmises, en plus de
    l'environnement minimal du SDK MCP (PATH, HOME…) : aucun autre secret.
    """
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(SERVERS_DIR / script)],
        "env": {key: value for key, value in env.items() if value},
    }


def _leaf(error: BaseException) -> BaseException:
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return error


def run_mcp(coroutine):
    """asyncio.run + déballage des ExceptionGroup d'anyio : l'erreur d'origine
    (ex. « Notion 401 ») remonte au lieu de « unhandled errors in a TaskGroup »."""
    try:
        return asyncio.run(coroutine)
    except BaseExceptionGroup as group:
        leaf = _leaf(group)
        raise RuntimeError(f"{type(leaf).__name__} : {leaf}") from leaf


def tool_payload(result) -> dict:
    """Les outils MCP renvoient des blocs de contenu ; on relit le JSON texte."""
    if isinstance(result, tuple):  # (contenu, artefact) selon les versions
        result = result[0]
    if isinstance(result, list):
        result = "".join(block.get("text", "") for block in result if isinstance(block, dict))
    try:
        return json.loads(result)
    except (TypeError, json.JSONDecodeError):
        raise RuntimeError(f"Réponse MCP illisible : {str(result)[:200]}")
