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
- Mémoire : `python -m app.main memory [--export]` · règles suggérées : `rules [--accept|--dismiss CLÉ]`
- Pourquoi cette veille ? : `python -m app.main why [--json]`, page `/why` (profil, mémoire typée, règles, ranking)
- Login web : `WEB_USERNAME` / `WEB_PASSWORD` (≥ 8 car., min, maj, chiffre, spécial ; sinon refus de démarrer) ;
  générer : `python -m app.main web-password [--write-env]` ; TUI et scripts en HTTP Basic
- Interface web de chat : `python -m app.main web` (http://127.0.0.1:8000) ; en arrière-plan :
  `bin/veille start|stop|restart|status|logs [-f]` (PID et journal dans `data/web.pid`, `data/web.log`)
- Rapport daté : `python -m app.main report` · Notion : `python -m app.main notion-sync`
- Instructions templatées : `python -m app.main instructions claude-md|demande|chat`
- Grill-me (profil de centres d'intérêt) : `python -m app.main grill`, skill `grill-me`, `grill-save`
- Review d'une actu par URL : `python -m app.main review <URL> [--json]`, skill `review-actu`, onglet 🔬 Review
- Base de connaissances : `python -m app.main knowledge [TERME] [--index] [--add fichier.md]`, onglet 📚 Knowledge
- Actus en cartes : `python -m app.main news crawl|search ["sujets"]|list|screenshots`, onglet 🗞️ Actus, bouton 🌐 (clé `CLAUDE_API`)
- Événements IA : `python -m app.main events search|crawl|list`, onglet 📅 (export `/api/events.ics`)
- Vidéos & podcasts : `python -m app.main media search|crawl|list`, onglet 🎬
- Benchmarks (BenchLM.ai) : `python -m app.main benchmarks crawl|list|show <clé>`, onglet 📊
- Cron quotidien (7 h, APScheduler) : `python -m app.main cron start|once|status` (`bin/veille cron …`,
  service `systemd/veille-cron.service`) : benchmarks, actus, événements via `bin/veille`
- Sources par URL (vérifiées) : `python -m app.main source add <URL>` · `source list` · `source remove <type> <valeur>`
- TUI (OpenTUI + React, Bun local) : `bin/veille tui [écran]` ; tests `cd tui && ./node_modules/.bin/bun test` ;
  exécutable autonome : `bin/veille tui-build [bun-linux-arm64]` → `tui/dist/veille-tui [écran]`
- Inspection SQLite : `sqlite3 data/watch.db`
- Vérification style : `python -m compileall app`

## Architecture
- Sources autorisées : `sources.toml` (skill `ajout-source` pour toute modification ; `app/sources_admin.py`
  l'automatise : détection, vérification, écriture relue par tomllib).
- Supervisor + Task Graph : `app/workflow/graph.py`, `app/workflow/tasks.py`.
  Qualité : sous-graphe `quality` (bruit → doublons → sélection, sans LLM) et ranking hybride explicable
  (`app/workflow/quality.py`, score /100 + raisons dans le digest ; `DEDUP_THRESHOLD`, `RANK_WEIGHTS`).
  Sous-agents (sous-graphes) : `app/workflow/subagents.py` ; prompts : `app/prompts/*.md`.
  Claims et preuves : sous-graphe `evidence` (`app/workflow/evidence.py`, prompt `claims.md`) : citation
  vérifiée mot pour mot, protocole des benchmarks, source secondaire jamais « confirmée » seule
  (`primary = false` dans sources.toml), contradictions affichées, confiance /100 ; table `claims` (v10).
  L'Editor sépare faits / analyse / hypothèse ; `guard_numbers` bloque un chiffre absent de la source.
  Sélection diversifiée des signaux (`DIVERSITY_PENALTY`).
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
- Profil d'impact versionné : `profiles/julien.toml` (`app/profile.py`, `IMPACT_PROFILE_PATH`) → ranking,
  Editor, « Pour toi » par item, `Digest.profile_version`. Mémoire typée (`MemoryRecord` : type, provenance,
  confiance, expiration) et règles suggérées après rejets récurrents (`app/memory.py`), page `/why` (`app/why.py`).
- Knowledge : `knowledge/*.md` (glossaire, règles métiers par domaine, prompts ; format dans `knowledge/README.md`),
  chargés par `app/knowledge.py` ; téléversements validés dans `data/knowledge/` (hors Git), jamais dans `knowledge/`.
- Review : `app/review.py` (agent Reviewer fetch → analyze → guard → save ; SSRF bloquée, citations vérifiées
  dans la page), table `reviews` (v5), prompt `app/prompts/review.md` ; challenge = outil de chat `challenge_review`.
- Actus : `app/news.py` (crawl `[[blog]]` HTML + flux `[news].rss` ; recherche web API Claude `web_search`, URL
  gardées seulement si présentes dans les résultats), table `news` (v6), prompt `app/prompts/news_search.md`.
- Événements : `app/events.py` (recherche web Claude, date gardée seulement si retrouvée dans la page ; flux `.ics`
  `[[events]]`), table `events` (v7), prompt `app/prompts/events_search.md`.
- Médias : `app/media.py` (flux `[[media]]` YouTube/podcasts + recherche web Claude), table `media` (v8),
  prompt `app/prompts/media_search.md`.
- Benchmarks : `app/benchmarks.py` (catalogue BenchLM via `__NEXT_DATA__`, détail à la demande en cache ;
  analyse déterministe : leader, meilleur modèle à poids ouverts, saturation, provenance, fraîcheur), table `benchmarks` (v9).
- Cron : `app/scheduler.py` (tâches = commandes `bin/veille`, fraîcheur `CRON_MIN_INTERVAL_HOURS`, essais,
  rattrapage, report hors ligne, verrou) ; état `data/cron-state.json`, journal `data/cron.log` (hors Git).
- Aperçus des actus : `app/screenshots.py` via MCP Playwright (`@playwright/mcp@0.0.82`, `SCREENSHOT_ENABLED`),
  images dans `data/screenshots/` (hors Git) ; aussi déclaré pour Claude Code dans `.mcp.json`.
- Login web : `app/web/auth.py` (sessions HMAC, anti-force brute), page `app/web/static/login.html`.
- Assistant Claude flottant : `app/assistant.py` (API Claude directe, streaming, `/api/assistant`) ; clé `CLAUDE_API`,
  `CLAUDE_WORKSPACE_ID` si la clé n'est rattachée à aucun workspace. Publication d'une review dans Notion :
  `app/notion.py` (`publish_review`, append sur la page de veille active, après confirmation dans l'interface).
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
