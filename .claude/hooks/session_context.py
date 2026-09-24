#!/usr/bin/env python3
"""Hook SessionStart : injecte l'état de la veille dans le contexte de Claude.

Ce qui est imprimé sur stdout est ajouté au contexte de la session.
Bibliothèque standard uniquement ; lecture seule de data/watch.db.
"""

import json
import os
import sqlite3
from pathlib import Path

root = Path(os.environ.get("CLAUDE_PROJECT_DIR", Path(__file__).resolve().parents[2]))
database = root / "data" / "watch.db"

if not database.exists():
    print("Veille : aucune base encore. Lancer `python -m app.main doctor` puis `run`.")
    raise SystemExit(0)

connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
try:
    runs = connection.execute(
        "SELECT run_id, status, started_at, stats_json FROM runs ORDER BY started_at DESC LIMIT 5"
    ).fetchall()
    counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("documents", "published_items", "digests")
    }
except sqlite3.Error as error:
    print(f"Veille : base illisible ({error}).")
    raise SystemExit(0)

lines = [
    "## État de la veille (hook SessionStart)",
    f"- {counts['documents']} documents en mémoire, {counts['published_items']} items publiés "
    f"dans {counts['digests']} digest(s).",
]
for run_id, status, started_at, stats_json in runs:
    stats = json.loads(stats_json or "{}")
    detail = f", {stats['accepted']} accepté(s)" if "accepted" in stats else ""
    lines.append(f"- run {run_id[:8]} ({started_at}) : {status}{detail}")
    if status == "awaiting_approval":
        lines.append(f"  → en attente : `python -m app.main resume {run_id} --approved|--rejected`")
print("\n".join(lines))
