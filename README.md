<div align="center">

# ✦ Scouty

### Veille GenAI pour la WAX

**Une veille LLM / GenAI factuelle, sourcée et exploitable** — produite par des agents LangGraph
sur un modèle local (Ollama) ou Claude, encadrée par un *harness* : règles, guards, hooks, mémoire
et validation humaine. Chaque affirmation publiée renvoie à sa source primaire.

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-agents-1C3C3C?logo=langchain&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-web-009688?logo=fastapi&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063?logo=pydantic&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-WAL-003B57?logo=sqlite&logoColor=white)
![Ollama](https://img.shields.io/badge/Ollama-local-000000?logo=ollama&logoColor=white)
![Claude](https://img.shields.io/badge/Claude-API-D97757?logo=anthropic&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-GitHub%20·%20Notion%20·%20Playwright-6E56CF)
![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)
![Jetson](https://img.shields.io/badge/NVIDIA-Jetson%20Orin-76B900?logo=nvidia&logoColor=white)

🎞️ **Slides : [github.com/Symfomany/wax-demo](https://github.com/Symfomany/wax-demo)**

[Démarrage rapide](#-démarrage-rapide) · [Stack](#-stack-technique) · [Fonctionnalités](#-fonctionnalités) ·
[Architecture](#️-architecture) · [Commandes](#️-commandes) · [Documentation](#-documentation)

</div>

---

## 📊 Le dashboard

<div align="center">

![Scouty — dashboard des actus](docs/screenshots/web-news.png)

<sub>Onglet 🗞️ <b>Actus</b> : blogs crawlés, flux officiels et recherche web Claude, en cartes ou en liste.</sub>

</div>

| 🛰️ Veille en direct, validation humaine | 💬 Chat sourcé, sources en cartes |
|:--:|:--:|
| ![Veille](docs/screenshots/web-veille.png) | ![Chat](docs/screenshots/web-chat-sources.png) |
| **🔬 Review d'une URL** | **✳ Assistant Claude flottant** |
| ![Review](docs/screenshots/web-review.png) | ![Assistant](docs/screenshots/web-assistant.png) |
| **📚 Base de connaissances** | **🧭 Sources par URL** |
| ![Knowledge](docs/screenshots/web-knowledge.png) | ![Sources](docs/screenshots/web-sources.png) |

> Captures réelles : interface web rendue par Chromium, TUI par le moteur d'OpenTUI
> (`tui/scripts/snapshot.tsx`) ; données et réponses produites par `gemma-3-4b-it` en local.

<details>
<summary><b>🖥️ La TUI (terminal) — 10 écrans</b></summary>

| 🏠 Accueil | 💬 Chat en streaming |
|:--:|:--:|
| ![TUI accueil](docs/screenshots/tui-home.png) | ![TUI chat](docs/screenshots/tui-chat.png) |
| **🔬 Review** | **🔬 Challenge de la review** |
| ![TUI review](docs/screenshots/tui-review.png) | ![TUI challenge](docs/screenshots/tui-challenge.png) |
| **🛰️ Veille : étapes, journal, digest** | **🧭 Source vérifiée avant ajout** |
| ![TUI veille](docs/screenshots/tui-watch.png) | ![TUI sources](docs/screenshots/tui-sources.png) |
| **📚 Knowledge** | **🧠 Mémoire** |
| ![TUI knowledge](docs/screenshots/tui-knowledge.png) | ![TUI mémoire](docs/screenshots/tui-memory.png) |
| **🎯 Grill-me** | **📰 Rapports datés** |
| ![TUI grill](docs/screenshots/tui-grill.png) | ![TUI rapports](docs/screenshots/tui-reports.png) |

</details>

---

## 🧱 Stack technique

| Couche | Outils | Où |
|:--|:--|:--|
| 🐍 **Langage** | Python ≥ 3.12, `.venv` (`uv` sur la Jetson) | `pyproject.toml` |
| 🕸️ **Orchestration** | LangGraph : Supervisor + Task Graph, sous-graphes, `interrupt()`, checkpoint SQLite, Store | `app/workflow/` |
| 🧠 **LLM** | Ollama (local, `gemma-3-4b-it`), Claude (API Anthropic), OpenAI — via LangChain | `app/llm.py` |
| 📐 **Contrats** | Pydantic v2 + pydantic-settings : toute sortie LLM validée avant persistance | `app/schemas.py`, `app/config.py` |
| 🗄️ **Persistance** | SQLite (WAL), migrations versionnées v1 → v10 | `app/storage.py` |
| 🔌 **MCP** | serveurs `github-scout` (lecture seule) et `notion-veille` ; Playwright MCP pour les aperçus | `app/mcp_servers/`, `.mcp.json` |
| 🌐 **Web** | FastAPI + uvicorn, streaming SSE, HTML/CSS/JS sans framework, login HMAC | `app/web/` |
| 🖥️ **TUI** | OpenTUI + React 19, Bun | `tui/` |
| ⌨️ **CLI** | Typer + rich, lanceur `bin/veille` | `app/main.py`, `bin/` |
| 📝 **Publication** | Jinja2 (rapport Markdown/HTML), Notion | `app/publishers.py`, `app/notion.py` |
| 📡 **Collecte** | httpx, feedparser (RSS/Atom, arXiv), API GitHub, recherche web Claude | `app/collectors/` |
| ⏰ **Planification** | APScheduler (cron quotidien), systemd | `app/scheduler.py`, `systemd/` |
| 🔭 **Observabilité** | Langfuse, LangSmith, Prometheus (`/metrics`) + Grafana, table `traces` | `app/observability.py`, `monitoring/` |
| 🐳 **Déploiement** | Docker Compose (web + cron, Ollama natif), NVIDIA Jetson Orin 8 Go | `Dockerfile`, `compose.yaml`, `docker/` |
| 💡 **Matériel** | anneau LED ESP32 et bouton Zigbee en MQTT (paho-mqtt), bouton d'alimentation | `app/led.py`, `app/power_button.py` |
| 🤖 **Harness Claude Code** | skills, sous-agents, hooks, mémoire exportée | `.claude/` |
| 🧪 **Tests** | pytest (unitaires + E2E, faux LLM/GitHub/Notion), `bun test` | `tests/`, `tui/` |

---

## ✨ Fonctionnalités

| | Fonctionnalité | Où |
|---|---|---|
| 🛰️ | **Veille multi-agents** : collecte parallèle (RSS, arXiv, releases GitHub, GitHub via MCP) → qualité (bruit, doublons, ranking /100) → Scout → Critic → preuves → Editor → **validation humaine** → rapport daté + Notion | Web · TUI · CLI |
| 🗞️ | **Actus en cartes** : `claude.com/blog` crawlé, flux OpenAI, NVIDIA, Hugging Face, GitHub ; bouton **🌐 Mes actus via Claude** (outil `web_search`, seules les URL réellement trouvées sont gardées) | Web · CLI |
| 📅 | **Événements IA** : recherche web Claude et flux `.ics`, date gardée seulement si retrouvée dans la page ; export `/api/events.ics` | Web · CLI |
| 🎬 | **Vidéos & podcasts** : flux YouTube / podcasts et recherche web | Web · CLI |
| 📊 | **Benchmarks** (BenchLM.ai) : leader, meilleur modèle à poids ouverts, saturation, fraîcheur | Web · CLI |
| 💬 | **Chat sourcé** en streaming : recherche plein texte, digests, GitHub via MCP, glossaire ; chaque réponse cite ses sources `[n]` | Web · TUI |
| 🔬 | **Review d'une actu par URL** : affirmations **vérifiées mot pour mot dans la page**, puis **challenge** par chat et révision | Web · TUI · CLI |
| 📚 | **Base de connaissances** : glossaire IA/GenAI/LLM sourcé, règles métiers par domaine, prompts cliquables, téléversement Markdown | Web · TUI · CLI |
| 🧭 | **Sources par URL** : blog, flux, dépôt ou catégorie arXiv — identifiée, **vérifiée**, puis ajoutée à `sources.toml` | Web · TUI · CLI |
| 🎯 | **Grill-me** + **profil d'impact** versionné : tes centres d'intérêt guident le ranking, le Scout et l'Editor | Web · TUI · CLI |
| 🧠 | **Mémoire typée** : leçons des validations, règles suggérées après rejets récurrents, page **`/why`** (pourquoi cette veille ?) | Web · CLI |
| ✳ | **Assistant Claude** flottant : API Claude directe en streaming, actus récentes en contexte | Web |
| 🔔 | **Notifications** : toasts, centre 🔔, notification système en fin de tâche | Web |
| 📝 | **Page Notion** mise en forme : couverture, fiches illustrées (`og:image`), veilles précédentes repliées | Web · TUI · CLI |
| 🧩 | **How to** (`/howto`) : schémas interactifs pour débutants, alimentés par les vrais graphes LangGraph | Web |
| 🧭 | **Traçabilité** : parcours de chaque réponse dans le graphe (`/trace/<id>`), liens Langfuse déterministes | Web |

### 🛰️ Le pipeline de veille

```mermaid
flowchart LR
    S[Supervisor] --> C{{Collecte parallèle}}
    C --> R1[RSS] & R2[arXiv] & R3[Releases GitHub] & R4[GitHub via MCP]
    R1 & R2 & R3 & R4 --> Q[Qualité<br/>bruit · doublons · ranking]
    Q --> SC[🤖 Scout<br/>pertinence · nouveauté]
    SC --> CR[🤖 Critic<br/>vérification factuelle]
    CR --> EV[🔎 Preuves<br/>citations · confiance]
    EV --> ED[🤖 Editor<br/>faits · analyse · hypothèse]
    ED --> G[🛡️ Guards<br/>URL · dates · chiffres]
    G --> H{✋ Validation humaine}
    H -->|publier| PUB[📰 Rapport daté<br/>📝 Notion]
    H -->|rejeter + motif| M[(🧠 Leçon mémorisée)]
```

- Le LLM ne renvoie que des **identifiants** : URL, titre et date sont recopiés par le code depuis les documents collectés.
- **Claims et preuves** : citation vérifiée mot pour mot, une source secondaire n'est jamais « confirmée » seule, contradictions affichées, confiance /100.
- `guard_numbers` bloque tout chiffre absent de la source ; toute sortie LLM est validée par **Pydantic**.
- Veille **ciblée** : mots-clés (OU / ET), sources, âge maximal, nombre de candidats — depuis la recherche, le chat ou le profil Grill-me.

<details>
<summary><b>🔬 Review d'une actualité par URL</b></summary>

Agent **Reviewer** (`app/review.py`) : `fetch → analyze → guard → save`.

1. **fetch** : téléchargement borné (http(s) public uniquement, chaque redirection revérifiée — protection SSRF), extraction du texte principal. Titre, site et **date viennent des métadonnées de la page**, jamais du LLM.
2. **analyze** : domaines détectés de façon déterministe, puis synthèse guidée par le skill [`review-actu`](.claude/skills/review-actu/SKILL.md), les **règles métiers numérotées** du domaine, le glossaire et la mémoire de veille.
3. **guard** : chaque affirmation doit **citer un passage présent dans la page** — sinon elle est marquée *non étayée* et la confiance est plafonnée ; toute URL étrangère à l'article est retirée.
4. **save** : record validé par Pydantic, table SQLite `reviews`.

Le **challenge** se fait dans un chat dédié : l'assistant reconnaît une erreur quand le texte la contredit,
sinon défend la review en citant [1]. **♻️ Réviser** relance le Reviewer avec les objections du débat.
</details>

<details>
<summary><b>📚 Base de connaissances</b></summary>

Fichiers Markdown dans [`knowledge/`](knowledge/README.md) (dépôt) et `data/knowledge/` (téléversés, hors Git) :
`glossaire.md` (définitions avec **source primaire vérifiée**), `regles-metiers.md` (LLM, Inférence, RAG,
Agents, Évaluation, Sécurité, Réglementation…), `prompts-veille.md` (prompts cliquables).

```markdown
---
title: Mes notes RAG
type: glossaire        # glossaire | regles | prompts | note
---

## Late chunking
Domaine : RAG
Alias : chunking tardif
Mots-clés : embeddings, contexte
Source : https://url-primaire

Définition en Markdown.
```

Les fichiers téléversés sont **validés avant écriture** (format, URLs des sources, 200 ko max).
</details>

<details>
<summary><b>🧭 Sources par URL</b></summary>

`bin/veille source add https://blog.vllm.ai` automatise le skill [`ajout-source`](.claude/skills/ajout-source/SKILL.md) :

| URL collée | Détection | Vérification |
|---|---|---|
| `github.com/owner/repo` | dépôt GitHub | releases publiées via l'API GitHub |
| `arxiv.org/list/cs.LG` | catégorie arXiv | flux d'annonces `rss.arxiv.org` |
| flux RSS/Atom | flux direct | entrées datées |
| page de blog | `<link rel="alternate">`, puis `/feed`, `/rss.xml`… | entrées datées |

Refus automatiques : presse et agrégateurs, flux sans entrée datée, dépôt sans release, adresse non
publique. `sources.toml` est modifié **en préservant commentaires et mise en forme**, puis relu par `tomllib`.
</details>

<details>
<summary><b>🖥️ TUI — navigation</b></summary>

| Touche | Écran | Touche | Écran |
|---|---|---|---|
| `1` | 🏠 Accueil | `6` | 📚 Knowledge |
| `2` | 💬 Chat | `7` | 🧭 Sources |
| `3` | 🔎 Recherche | `8` | 📰 Rapports |
| `4` | 🔬 Review | `9` | 🧠 Mémoire + Notion |
| `5` | 🛰️ Veille | `0` | 🎯 Grill-me |

Chiffres, `←` `→`, `F1`–`F10`, `Échap` / `i` ou `/` (saisie), `Tab`, `?` (aide), `q` (quitter).
La TUI parle à l'API du serveur web et le **démarre automatiquement** s'il ne répond pas.
Exécutable autonome : `bin/veille tui-build [bun-linux-arm64]`.
</details>

---

## 🚀 Démarrage rapide

**Prérequis** : Python ≥ 3.12, [Ollama](https://ollama.com) (ou une clé API Claude), Bun/Node pour la TUI.

```bash
# 1. Environnement Python
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

# 2. Modèle local (ou LLM_PROVIDER=anthropic, voir docs/llm-ollama-claude.md)
ollama pull gemma-3-4b-it

# 3. Configuration : copier puis compléter (WEB_USERNAME / WEB_PASSWORD requis)
cp .env.example .env
.venv/bin/python -m app.main web-password      # génère un mot de passe conforme

# 4. Diagnostic : Ollama, modèle, GPU, sources, MCP, migrations
.venv/bin/python -m app.main doctor

# 5. C'est parti
bin/veille start     # interface web en arrière-plan → http://127.0.0.1:8000
bin/veille tui       # TUI
bin/veille cycle     # ou : une veille complète en ligne de commande
```

**Avec Docker** (web + cron, Ollama natif sur l'hôte) :

```bash
docker compose up -d --build
docker/install-service.sh        # Jetson : démarrage au boot (systemd)
```

Guide pas à pas (Notion, Langfuse, LangSmith) : [docs/getting-started.html](docs/getting-started.html) ·
Jetson Orin 8 Go : [docs/jetson-orin.md](docs/jetson-orin.md).

---

## 🏗️ Architecture

```mermaid
flowchart TB
    subgraph Clients
        WEB[🌐 Interface web<br/>app/web/static]
        TUI[🖥️ TUI OpenTUI + React<br/>tui/]
        CLI[⌨️ bin/veille · app.main]
        CRON[⏰ Cron APScheduler]
    end
    subgraph Serveur["FastAPI — app/web/server.py"]
        CHAT[💬 Agent de chat<br/>route → act → respond → guard]
        RUNS[🛰️ Veilles<br/>Supervisor + Task Graph]
        REV[🔬 Reviewer]
        NEWS[🗞️ Actus · 📅 Événements · 🎬 Médias · 📊 Benchmarks]
        KB[📚 Knowledge · 🧭 Sources]
    end
    subgraph Données
        SQL[(SQLite watch.db<br/>documents · digests · claims · reviews · traces)]
        STORE[(Store LangGraph<br/>mémoire typée)]
        MD[/knowledge/*.md · sources.toml · profiles/]
    end
    subgraph Extérieur
        LLM[Ollama / Claude]
        MCPG[MCP github-scout]
        MCPN[MCP notion-veille]
        MCPP[MCP Playwright]
    end
    WEB & TUI --> Serveur
    CLI & CRON --> RUNS & REV & NEWS & KB
    CHAT & RUNS & REV & NEWS --> LLM
    RUNS --> MCPG
    RUNS & CHAT --> MCPN
    NEWS --> MCPP
    Serveur --> SQL & STORE & MD
```

```text
app/
├── workflow/        Supervisor, Task Graph, sous-graphes (qualité, preuves, Scout, Critic, Editor)
├── chat/            agent de chat + outils (recherche, digests, GitHub, Notion, knowledge, challenge)
├── collectors/      RSS, arXiv, releases GitHub, GitHub via MCP
├── harness/         guards (MCP, injections, contrats d'état), hooks de publication, prompts
├── mcp_servers/     serveurs MCP github-scout et notion-veille
├── web/             serveur FastAPI + interface web + login
├── review.py        agent Reviewer          news.py · events.py · media.py · benchmarks.py
├── knowledge.py     base de connaissances   sources_admin.py   sources par URL
├── memory.py        mémoire typée + règles  why.py             page « pourquoi cette veille ? »
├── scheduler.py     cron quotidien          notion.py          page Notion via MCP
└── storage.py       SQLite + migrations (v1 → v10)
tui/                 TUI OpenTUI + React (Bun)
knowledge/           glossaire, règles métiers, prompts (Markdown)
profiles/            profil d'impact versionné
monitoring/          Prometheus, Grafana, exportateur Jetson
.claude/             skills, sous-agents, hooks et mémoire pour Claude Code
```

---

## ⌨️ Commandes

Toutes les commandes passent par le lanceur `bin/veille` (`bin/veille help`).

| Commande | Rôle |
|---|---|
| `bin/veille cycle [-k MOT…]` | Veille complète : run → digest → publier/rejeter → rapport |
| `bin/veille run [options]` | Veille sans décision (`--no-collect`, `--no-mcp`, `-k`, `-s`, `--max-age`, `--max-docs`, `--match-all`) |
| `bin/veille approve <run> ["note"]` · `reject <run> "motif"` | Publier / rejeter un run ; la note devient une leçon |
| `bin/veille pending` · `show <run>` | Runs en attente, digest d'un run |
| `bin/veille review <url> [--json]` | Review sourcée d'une actualité |
| `bin/veille news crawl\|search\|list` · `events …` · `media …` · `benchmarks …` | Actus, événements, médias, benchmarks |
| `bin/veille knowledge [terme] [--index] [--add f.md]` | Base de connaissances |
| `bin/veille source add <url>` · `list` · `remove <type> <valeur>` | Sources (vérifiées avant ajout) |
| `bin/veille start` · `stop` · `restart` · `status` · `logs [-f]` | Serveur web en arrière-plan |
| `bin/veille tui [écran]` · `grill` · `cron start\|once\|status` | TUI, Grill-me, cron quotidien |
| `bin/veille notion` · `report` · `memory` · `doctor` · `test` | Notion, rapport daté, mémoire, diagnostic, tests |

Équivalents Python : `python -m app.main <commande>` (voir [CLAUDE.md](CLAUDE.md)).

---

## ⚙️ Configuration

Tout se règle dans `.env` (modèle : [.env.example](.env.example)) — jamais versionné.

| Variable | Rôle | Défaut |
|---|---|---|
| `LLM_PROVIDER` · `LLM_MODEL` · `LLM_BASE_URL` | `ollama` \| `openai` \| `anthropic`, modèle, URL | `ollama` · `gemma-3-4b-it` |
| `ANTHROPIC_API_KEY` · `CLAUDE_API` | clé Claude (fournisseur `anthropic`) · recherche web et assistant | — |
| `NEWS_MODEL` · `ASSISTANT_MODEL` | modèles Claude des actus et de l'assistant | voir `.env.example` |
| `MAX_LLM_CALLS` · `MAX_DOCUMENTS_PER_RUN` · `MAX_AGE_DAYS` | budgets d'une veille | 20 · 24 · 14 |
| `HUMAN_APPROVAL` | arrêt avant publication | `true` |
| `WEB_USERNAME` · `WEB_PASSWORD` | login web (mot de passe fort exigé) | — |
| `WEB_HOST` · `WEB_PORT` | serveur web | `127.0.0.1` · 8000 |
| `GITHUB_TOKEN` · `NOTION_TOKEN` · `NOTION_PARENT_PAGE_ID` | GitHub, publication Notion | — |
| `MQTT_*` · `LED_*` | bouton Zigbee, anneau LED (facultatif) | voir `.env.example` |
| `LANGFUSE_*` · `LANGSMITH_*` | observabilité (facultatif) | désactivée |

---

## 🛡️ Sécurité et règles du harness

- **Rien d'inventé** : dates, versions, URL et chiffres viennent des sources ; les guards retirent les URL non sourcées.
- **Contenus non fiables** : injections de prompt neutralisées avant d'entrer dans un prompt (`sanitize_untrusted`).
- **SSRF** : review et ajout de sources refusent les adresses non publiques, y compris après redirection.
- **MCP** : GitHub en lecture seule (liste blanche d'outils) ; Notion limité à la page de veille.
- **Secrets** : `.env` jamais versionné ; hooks Claude Code bloquant secrets probables, `rm -rf` et lecture de `.env`.
- **Web** : écoute sur `127.0.0.1`, sessions HMAC, anti-force brute.
- **Schéma SQLite** : toute modification = une migration + un test.

Règles complètes : [CLAUDE.md](CLAUDE.md).

---

## 🧪 Tests

```bash
.venv/bin/python -m pytest -q                                        # unitaires + E2E (faux LLM, GitHub, Notion)
cd tui && ./node_modules/.bin/bun test                               # TUI : logique + écrans rendus
RUN_LIVE=1 .venv/bin/python -m pytest -q tests/e2e/test_live.py -s   # E2E réel (Ollama + réseau)
```

---

## 📖 Documentation

- [Getting started](docs/getting-started.html) — Ollama, installation, Notion, Langfuse, LangSmith
- [Documentation complète](docs/index.html)
- [LangGraph dans le projet](docs/langgraph.md)
- [LLM : Ollama, API Claude](docs/llm-ollama-claude.md)
- [Mise à jour et idées](docs/UPGRADE.md) · [Jetson Orin 8 Go](docs/jetson-orin.md)
- [Base de connaissances : format](knowledge/README.md)

<div align="center">
<sub>✦ Scouty — Veille GenAI pour la WAX</sub>
</div>
