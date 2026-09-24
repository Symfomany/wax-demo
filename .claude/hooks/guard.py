#!/usr/bin/env python3
"""Hook PreToolUse : bloque les commandes destructives et les fuites de secrets.

Code de sortie 2 = action refusée ; le message stderr est renvoyé à Claude.
Uniquement la bibliothèque standard : le hook tourne hors du venv.
"""

import json
import re
import sys

DANGEROUS_BASH = [
    (r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", "rm -rf : suppression récursive forcée"),
    (r"\brm\b[^|;&]*\b(data|output)/?\b", "suppression de la mémoire (data/) ou des digests (output/)"),
    (r"\bgit\s+push\b[^|;&]*(--force|-f\b)", "git push --force"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f)", "réinitialisation Git destructive"),
    (r"\b(DROP\s+(TABLE|INDEX|VIEW)|DELETE\s+FROM|TRUNCATE\s+TABLE)\b", "requête SQL destructive"),
    (r"(curl|wget)[^|]*\|\s*(ba|z)?sh\b", "exécution d'un script distant"),
    (r"\bgit\s+add\b[^|;&]*\.env\b(?!\.example)", "ajout de .env à Git"),
    (r"(cat|less|more|head|tail|bat)\s+[^|;&]*\.env\b(?!\.example)", "affichage de .env (secrets)"),
    # vraie redirection shell (> .env, >> ./.env, 2> .env) — pas « <code>.env</code> » en HTML
    (r"(?:^|[\s;&|(])\d?>>?\s*(?:\./)?\.env\b(?!\.example)", "écriture dans .env"),
]

SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9_\-]{20,}",
    r"gh[pousr]_[A-Za-z0-9]{30,}",
    r"github_pat_[A-Za-z0-9_]{30,}",
    r"hf_[A-Za-z0-9]{30,}",
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
]


MCP_WRITE_ACTIONS = (
    r"(create|update|delete|merge|push|fork|add|remove|close|reopen|assign|"
    r"request|submit|dismiss|rerun|cancel|run|trigger|star|unstar)_"
)


NOTION_SERVERS = {"notion-veille", "notion"}
NOTION_READ_ACTIONS = {
    "search_pages", "API-post-search", "API-retrieve-a-page", "API-get-block-children",
    "API-retrieve-a-block", "API-retrieve-page-markdown", "API-get-self", "API-get-user", "API-get-users",
}


def ask(reason: str) -> None:
    """Décision « ask » : Claude Code demande confirmation à l'utilisateur."""
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


def deny(reason: str) -> None:
    print(f"Bloqué par le hook du harness : {reason}. "
          "Demander une confirmation explicite à l'utilisateur.", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    event = json.load(sys.stdin)
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input", {})

    if tool == "Bash":
        command = tool_input.get("command", "")
        for pattern, reason in DANGEROUS_BASH:
            if re.search(pattern, command, re.IGNORECASE):
                deny(reason)

    if tool in {"Edit", "Write", "MultiEdit"}:
        path = tool_input.get("file_path", "")
        if re.search(r"(^|/)\.env$", path):
            deny("modification de .env sans demande explicite (CLAUDE.md)")
        content = " ".join(
            str(tool_input.get(key, "")) for key in ("content", "new_string")
        ) + " ".join(str(edit.get("new_string", "")) for edit in tool_input.get("edits", []))
        for pattern in SECRET_PATTERNS:
            if re.search(pattern, content):
                deny(f"secret probable dans {path} (motif {pattern})")

    # Outils MCP (github-scout ou serveur GitHub officiel) : lecture seule.
    if tool.startswith("mcp__"):
        server, action = tool.split("__")[1], tool.rsplit("__", 1)[-1]
        # Notion : seule destination d'écriture autorisée, toujours après confirmation.
        if server in NOTION_SERVERS:
            if action in NOTION_READ_ACTIONS:
                sys.exit(0)
            ask(f"écriture Notion ({action}) : publier la page de veille ?")
        if re.match(MCP_WRITE_ACTIONS, action):
            deny(f"outil MCP en écriture ({tool}) : la veille est en lecture seule")

    sys.exit(0)


if __name__ == "__main__":
    main()
