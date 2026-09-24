# LLM Watch Harness

Veille LLM / GenAI factuelle et sourcée, produite par des agents LangGraph sur un modèle local
(Ollama), encadrée par un harness : règles, skills, sous-agents, hooks, guards et mémoire.

- **Démarrer :** [docs/getting-started.html](docs/getting-started.html) — Ollama, installation, quoi lancer, tests, Notion, Langfuse, LangSmith.
- **Documentation complète :** [docs/index.html](docs/index.html).

```bash
python -m app.main doctor        # diagnostic : Ollama, modèle, GPU, MCP, migrations
python -m app.main web           # interface de chat : http://127.0.0.1:8000
python -m app.main run           # veille en ligne de commande (arrêt avant publication)
python -m app.main resume <run_id> --approved --note "…"
python -m pytest -q              # tests unitaires + E2E, hors ligne
```

Chaque veille publiée produit un rapport daté `reports/AAAA/veille-AAAA-MM-JJ-HHMM.md` et, si
Notion est configuré, met à jour la page « Veille GenAI — 10 dernières veilles ».
