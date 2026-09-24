#!/usr/bin/env python3
"""Hook PostToolUse : vérifie la syntaxe Python et rappelle la règle des migrations."""

import json
import py_compile
import sys

event = json.load(sys.stdin)
path = event.get("tool_input", {}).get("file_path", "")

if path.endswith(".py"):
    try:
        py_compile.compile(path, doraise=True)
    except py_compile.PyCompileError as error:
        print(f"Erreur de syntaxe après édition : {error.msg}", file=sys.stderr)
        sys.exit(2)

if path.endswith("app/storage.py"):
    print(
        "Rappel CLAUDE.md : toute modification du schéma SQLite exige une nouvelle "
        "entrée dans MIGRATIONS et un test dans tests/test_storage.py.",
        file=sys.stderr,
    )
    sys.exit(2)
