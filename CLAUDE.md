# Projet : LLM Watch Harness

## But
Produire une veille LLM/GenAI factuelle, sourcée et exploitable.

## Règles non négociables
- Ne jamais inventer une date, un benchmark, une version, une URL ou une annonce.
- Toute affirmation publiée doit être rattachée à une URL source.
- Préférer les sources primaires : release notes, articles arXiv, dépôts GitHub,
  blogs techniques officiels et documentation éditeur.
- Ne jamais stocker tokens, clés API ou secrets dans Git.
- Ne jamais modifier `.env` sans demande explicite.
- Toute modification du schéma SQLite doit avoir une migration et un test.
- Toute sortie LLM doit être validée par Pydantic avant persistance.
- Toute commande destructive requiert une confirmation explicite.
- Les outils MCP GitHub sont en lecture seule ; l'écriture Notion se limite à la page de veille,
  après confirmation.

## Commandes
- Lanceur : `bin/veille help` (cycle, run, resume, approve, reject, pending, show, grill, web…)
- Tests (unitaires + E2E) : `.venv/bin/python -m pytest -q`
- E2E réel (Ollama + réseau) : `RUN_LIVE=1 .venv/bin/python -m pytest -q tests/e2e/test_live.py -s`
- Diagnostic (Ollama, modèle, GPU, sources, MCP, migrations) : `python -m app.main doctor`
- Collecte + workflow : `python -m app.main run` (`--no-collect`, `--no-mcp`, `--approve`,
  veille ciblée : `-k MOT -s SOURCE --max-age N --max-docs N --match-all`)
- Validation humaine : `python -m app.main resume <run_id> --approved|--rejected --note "..."`
- Plan d'exécution : `python -m app.main plan --langgraph`
- Recherche GitHub via MCP : `python -m app.main github "llm inference" --limit 5`
- Mémoire : `python -m app.main memory [--export]`
- Interface web de chat : `python -m app.main web` (http://127.0.0.1:8000) ; en arrière-plan :
  `bin/veille start|stop|restart|status|logs [-f]` (PID et journal dans `data/web.pid`, `data/web.log`)
- Rapport daté : `python -m app.main report` · Notion : `python -m app.main notion-sync`
- Instructions templatées : `python -m app.main instructions claude-md|demande|chat`
- Grill-me (profil de centres d'intérêt) : `python -m app.main grill`, skill `grill-me`, `grill-save`
- Review d'une actu par URL : `python -m app.main review <URL> [--json]`, skill `review-actu`, onglet 🔬 Review
- Base de connaissances : `python -m app.main knowledge [TERME] [--index] [--add fichier.md]`, onglet 📚 Knowledge
- Sources par URL (vérifiées) : `python -m app.main source add <URL>` · `source list` · `source remove <type> <valeur>`
- TUI (OpenTUI + React, Bun local) : `bin/veille tui [écran]` ; tests `cd tui && ./node_modules/.bin/bun test`
- Inspection SQLite : `sqlite3 data/watch.db`
- Vérification style : `python -m compileall app`

## Architecture
- Sources autorisées : `sources.toml` (skill `ajout-source` pour toute modification ; `app/sources_admin.py`
  l'automatise : détection, vérification, écriture relue par tomllib).
- Supervisor + Task Graph : `app/workflow/graph.py`, `app/workflow/tasks.py`.
  Sous-agents (sous-graphes) : `app/workflow/subagents.py` ; prompts : `app/prompts/*.md`.
  Le LLM ne renvoie que des identifiants ; URL, titre et date sont recopiés par
  le code depuis les documents collectés.
- Guards : `app/harness/guards.py` (MCP, injections, contrats d'état) et
  `app/harness/hooks.py` (garde-fous de publication).
- MCP GitHub : serveur `app/mcp_servers/github_server.py`, client
  `app/collectors/github_mcp.py`, déclaré pour Claude Code dans `.mcp.json`.
- Mémoire : SQLite (`app/storage.py`, migrations `MIGRATIONS`), Store LangGraph
  (`app/memory.py`), export Claude Code importé ci-dessous.
- Publication : `app/publishers.py` (fichiers → rapport `templates/report.md.j2` → Notion).
  Page Notion : `app/notion.py` (dernière veille développée avec images og:image, précédentes repliées).
- TUI : `tui/` (écrans `tui/src/screens/`, client de l'API web `tui/src/lib/api.ts`) ;
  captures du README : `tui/scripts/snapshot.tsx` + `tui/scripts/png.sh` → `docs/screenshots/`.
- MCP Notion : serveur `app/mcp_servers/notion_server.py`, client `app/notion.py`.
- Chat : agent `app/chat/` (route → act → respond → guard), serveur `app/web/`.
- Instructions templatées : `templates/claude/` (profil TOML + templates Jinja2).
- Traçage : `app/observability.py` (Langfuse, LangSmith ; session = run ou conversation).
- Hooks Claude Code : `.claude/hooks/`, déclarés dans `.claude/settings.json`.
- Fournisseurs LLM : `LLM_PROVIDER` = ollama | openai | anthropic (`app/llm.py`).
- Prompts éditables : surcharges dans `data/prompts/` (`app/harness/prompts.py`), jamais les fichiers du dépôt.
- Traces du graphe : table `traces` (v4), page `/trace/<id>` ; liens Langfuse déterministes (`app/observability.py`).
- Grill-me : `app/grill.py` (entretien par `interrupt()`), profil dans le Store (`WatchMemory.interests`).
- Knowledge : `knowledge/*.md` (glossaire, règles métiers par domaine, prompts ; format dans `knowledge/README.md`),
  chargés par `app/knowledge.py` ; téléversements validés dans `data/knowledge/` (hors Git), jamais dans `knowledge/`.
- Review : `app/review.py` (agent Reviewer fetch → analyze → guard → save ; SSRF bloquée, citations vérifiées
  dans la page), table `reviews` (v5), prompt `app/prompts/review.md` ; challenge = outil de chat `challenge_review`.
- Documentation : `docs/getting-started.html`, `docs/index.html`, `docs/langgraph.md`,
  `docs/llm-ollama-claude.md`, `docs/UPGRADE.md`, `docs/jetson-orin.md`.

## Mémoire de veille
@.claude/memory/veille.md

## Définition de terminé
- Sources visibles et URLs valides.
- JSON conforme au schéma Pydantic.
- Digest Markdown et JSON produits.
- Aucun secret dans les fichiers suivis par Git.
- Tests pertinents verts.
