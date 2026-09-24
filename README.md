# LLM Watch Harness

Veille LLM / GenAI factuelle et sourcée, produite par des agents LangGraph sur un modèle local
(Ollama), encadrée par un harness : règles, skills, sous-agents, hooks, guards et mémoire.

- **Démarrer :** [docs/getting-started.html](docs/getting-started.html) — Ollama, installation, quoi lancer, tests, Notion, Langfuse, LangSmith.
- **Documentation complète :** [docs/index.html](docs/index.html).
- **LangGraph dans le projet :** [docs/langgraph.md](docs/langgraph.md).
- **LLM : Ollama, API Claude (native et format OpenAI) :** [docs/llm-ollama-claude.md](docs/llm-ollama-claude.md).
- **Mise à jour et idées de fonctionnalités :** [docs/UPGRADE.md](docs/UPGRADE.md).
- **Déploiement sur Jetson Orin 8 Go :** [docs/jetson-orin.md](docs/jetson-orin.md).

```bash
bin/veille cycle                 # veille complète : run → digest → publier/rejeter → rapport
bin/veille grill                 # Grill-me : préciser ce que tu cherches en actu IA
bin/veille review <url>          # review sourcée d'une actu (puis challenge dans l'onglet 🔬 Review)
bin/veille knowledge "kv cache"  # base de connaissances : glossaire, index, règles métiers
bin/veille start                 # interface web en arrière-plan (stop, restart, status, logs -f)
bin/veille help                  # toutes les commandes du lanceur

python -m app.main doctor        # diagnostic : Ollama, modèle, GPU, MCP, migrations
python -m app.main web           # interface de chat : http://127.0.0.1:8000
python -m app.main run           # veille en ligne de commande (arrêt avant publication)
python -m app.main resume <run_id> --approved --note "…"
python -m pytest -q              # tests unitaires + E2E, hors ligne
```

L'interface web ajoute deux onglets : **📚 Knowledge** (glossaire IA/GenAI/LLM, index des mots-clés,
règles métiers par domaine, prompts cliquables, téléversement de fichiers Markdown — voir
[knowledge/README.md](knowledge/README.md)) et **🔬 Review** (URL → synthèse de l'agent Reviewer selon le
skill `review-actu` et les règles métiers, citations vérifiées dans la page, challenge par chat, révision).

Chaque veille publiée produit un rapport daté `reports/AAAA/veille-AAAA-MM-JJ-HHMM.md` (+ `.html`) et, si
Notion est configuré, met à jour la page « Veille GenAI — 10 dernières veilles ».
