# 🛰️ LLM Watch Harness

**Veille LLM / GenAI factuelle et sourcée**, produite par des agents LangGraph sur un modèle local
(Ollama) ou Claude, encadrée par un *harness* : règles, skills, sous-agents, hooks, guards et mémoire.
Chaque affirmation publiée renvoie à sa source primaire ; un humain valide avant publication.

Trois façons de l'utiliser : une **interface web**, une **TUI** (terminal, OpenTUI + React) et la **CLI**
`bin/veille`. Les veilles publiées produisent un rapport daté (Markdown + HTML) et une page **Notion**
mise en forme.

![TUI — accueil](docs/screenshots/tui-home.png)

---

## Sommaire

- [Ce que fait le projet](#-ce-que-fait-le-projet)
- [Captures d'écran](#-captures-décran)
- [Démarrage rapide](#-démarrage-rapide)
- [Commandes](#️-commandes)
- [Fonctionnalités en détail](#-fonctionnalités-en-détail)
- [Architecture](#️-architecture)
- [Configuration](#️-configuration)
- [Sécurité et règles du harness](#️-sécurité-et-règles-du-harness)
- [Tests](#-tests)
- [Documentation](#-documentation)

---

## ✨ Ce que fait le projet

| | Fonctionnalité | Où |
|---|---|---|
| 🛰️ | **Veille multi-agents** : collecte parallèle (RSS, arXiv, releases GitHub, découverte GitHub via MCP) → Scout → Critic → Editor → **validation humaine** → rapport daté + Notion | Web · TUI · CLI |
| 🗞️ | **Actus en cartes** (façon blog Anthropic) : crawl de `claude.com/blog` et d'OpenAI News, plus bouton **🌐 Mes actus via Claude** (API Claude + outil `web_search` sur ton profil) ; seules les URL réellement trouvées sont gardées | Web · CLI |
| ✳ | **Assistant Claude** flottant (repliable) : conversation directe avec l'API Claude (pas le LLM local), actus récentes en contexte, recherche web 🌐 en option | Web |
| 🔔 | **Notifications** : toasts animés, centre de notifications, notification du navigateur quand une recherche, un crawl, une review ou une veille se termine | Web |
| 💬 | **Chat sourcé** en streaming : recherche plein texte, derniers digests, GitHub via MCP, glossaire ; chaque réponse cite ses sources `[n]` et montre les agents engagés | Web · TUI |
| 🔬 | **Review d'une actu par URL** : scraping, synthèse selon les règles métiers du domaine, affirmations **vérifiées mot pour mot dans la page**, puis **challenge par chat** et révision | Web · TUI · CLI |
| 📚 | **Base de connaissances** : glossaire IA/GenAI/LLM (78 termes sourcés), index de mots-clés, règles métiers par domaine, prompts cliquables, **téléversement de fichiers Markdown** | Web · TUI · CLI |
| 🧭 | **Sources par URL** : collez un blog, un flux, un dépôt GitHub ou une catégorie arXiv — la source est identifiée, **vérifiée**, puis ajoutée à `sources.toml` | Web · TUI · CLI |
| 📝 | **Page Notion** mise en forme : couverture, résumé, description, fiches ressources illustrées, veilles précédentes repliées | Web · TUI · CLI |
| 🎯 | **Grill-me** : entretien pour cerner tes centres d'intérêt ; le profil guide le Scout et l'Editor | Web · TUI · CLI |
| 🧠 | **Mémoire** : leçons des validations humaines, thèmes appris, santé des sources, export vers Claude Code | Web · TUI · CLI |
| 🧭 | **Traçabilité** : parcours de chaque réponse dans le graphe (`/trace/<id>`), Langfuse et LangSmith en option | Web |

---

## 📸 Captures d'écran

> Captures réelles : la TUI est rendue par le moteur d'OpenTUI (caractères et couleurs exacts,
> `tui/scripts/snapshot.tsx`), l'interface web par Chromium ; données et réponses produites par
> `gemma-3-4b-it` en local.

### Interface web

**Actus en cartes** — blogs crawlés, flux officiels et recherche web Claude

![Actus](docs/screenshots/web-news.png)

| Chat : sources en cartes, « Voir plus » | Modale : résumé et contenu collecté |
|---|---|
| ![Chat](docs/screenshots/web-chat-sources.png) | ![Modale](docs/screenshots/web-modal.png) |
| **Recherche en cartes / liste** | **Assistant Claude flottant** |
| ![Recherche](docs/screenshots/web-search-cards.png) | ![Assistant](docs/screenshots/web-assistant.png) |

| Review d'une URL | Sources par URL |
|---|---|
| ![Review](docs/screenshots/web-review.png) | ![Sources](docs/screenshots/web-sources.png) |
| **Chat sourcé** | **Base de connaissances** |
| ![Chat](docs/screenshots/web-chat.png) | ![Knowledge](docs/screenshots/web-knowledge.png) |
| **Veille en direct (validation humaine)** | **Glossaire (lien direct `#k=…`)** |
| ![Veille](docs/screenshots/web-veille.png) | ![Glossaire](docs/screenshots/web-glossaire.png) |

### TUI (`bin/veille tui`)

| 💬 Chat en streaming | 🔬 Review d'une URL |
|---|---|
| ![TUI chat](docs/screenshots/tui-chat.png) | ![TUI review](docs/screenshots/tui-review.png) |
| **🔬 Challenge de la review** | **🛰️ Veille : étapes, journal, digest à valider** |
| ![TUI challenge](docs/screenshots/tui-challenge.png) | ![TUI veille](docs/screenshots/tui-watch.png) |
| **🧭 Source vérifiée avant ajout** | **📚 Knowledge** |
| ![TUI sources](docs/screenshots/tui-sources.png) | ![TUI knowledge](docs/screenshots/tui-knowledge.png) |
| **🎯 Grill-me** | **🧠 Mémoire** |
| ![TUI grill](docs/screenshots/tui-grill.png) | ![TUI mémoire](docs/screenshots/tui-memory.png) |

---

## 🚀 Démarrage rapide

**Prérequis** : Python ≥ 3.12, [Ollama](https://ollama.com) (ou une clé API Claude), Node.js + npm pour la TUI.

```bash
# 1. Environnement Python
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

# 2. Modèle local (ou LLM_PROVIDER=anthropic, voir docs/llm-ollama-claude.md)
ollama pull gemma-3-4b-it

# 3. Configuration : copier puis compléter (GITHUB_TOKEN, NOTION_TOKEN… facultatifs)
cp .env.example .env

# 4. Diagnostic : Ollama, modèle, GPU, MCP, migrations, accès Notion
.venv/bin/python -m app.main doctor

# 5. C'est parti
bin/veille start     # interface web en arrière-plan → http://127.0.0.1:8000
bin/veille tui       # TUI (installe ses dépendances au premier lancement, démarre le serveur au besoin)
bin/veille cycle     # ou : une veille complète en ligne de commande
```

Guide pas à pas (Notion, Langfuse, LangSmith) : [docs/getting-started.html](docs/getting-started.html).

---

## ⌨️ Commandes

Toutes les commandes passent par le lanceur `bin/veille` (`bin/veille help`).

| Commande | Rôle |
|---|---|
| `bin/veille cycle [-k MOT…]` | Veille complète : run → digest → publier/rejeter (interactif) → rapport |
| `bin/veille run [options]` | Veille sans décision (`--no-collect`, `--no-mcp`, `-k`, `-s`, `--max-age`, `--max-docs`, `--match-all`) |
| `bin/veille approve <run> ["note"]` · `reject <run> "motif"` | Publier / rejeter un run en attente ; la note devient une leçon |
| `bin/veille pending` · `show <run>` | Runs en attente, digest d'un run |
| `bin/veille review <url> [--json]` | Review sourcée d'une actualité |
| `bin/veille knowledge [terme] [--index] [--add f.md]` | Base de connaissances : recherche, index, ajout d'un fichier |
| `bin/veille source add <url>` · `list` · `remove <type> <valeur>` | Sources de la veille (vérifiées avant ajout) |
| `bin/veille news crawl` · `search ["sujets"]` · `list` | Actus en cartes : blogs crawlés, recherche web Claude |
| `bin/veille start [--port N]` · `stop` · `restart` · `status` · `logs [-f]` | Serveur web en arrière-plan (PID et journal dans `data/`) |
| `bin/veille web` | Serveur web au premier plan |
| `bin/veille tui [écran]` | TUI (écrans : `home chat search review watch knowledge sources reports memory grill`) |
| `bin/veille grill` | Entretien Grill-me dans le terminal |
| `bin/veille notion` · `report` · `memory` · `doctor` · `test` | Notion, rapport daté, mémoire, diagnostic, tests |

Équivalents Python : `python -m app.main <commande>` (voir [CLAUDE.md](CLAUDE.md)).

---

## 🔎 Fonctionnalités en détail

### 🛰️ Veille multi-agents

```mermaid
flowchart LR
    S[Supervisor] --> C{{Collecte parallèle}}
    C --> R1[RSS] & R2[arXiv] & R3[Releases GitHub] & R4[GitHub via MCP]
    R1 & R2 & R3 & R4 --> P[Préfiltre<br/>fraîcheur · doublons · focus]
    P --> SC[🤖 Scout<br/>pertinence · nouveauté]
    SC --> CR[🤖 Critic<br/>vérification factuelle]
    CR --> ED[🤖 Editor<br/>digest]
    ED --> G[🛡️ Guards<br/>URL · dates · contrats]
    G --> H{✋ Validation humaine}
    H -->|publier| PUB[📰 Rapport daté<br/>📝 Notion]
    H -->|rejeter + motif| M[(🧠 Leçon mémorisée)]
```

- Le LLM ne renvoie que des **identifiants** : URL, titre et date sont recopiés par le code depuis les documents collectés.
- Toute sortie LLM est validée par **Pydantic** avant persistance ; budgets d'appels et de documents par run.
- Veille **ciblée** : mots-clés (OU / ET), sources, âge maximal, nombre de candidats — depuis la recherche, le chat (« relance la veille sur MCP, agents ») ou le profil Grill-me.

### 🔬 Review d'une actualité par URL

Agent **Reviewer** (`app/review.py`) : `fetch → analyze → guard → save`.

1. **fetch** : téléchargement borné (http(s) public uniquement, chaque redirection revérifiée — protection SSRF), extraction du texte principal (`<article>` / `<main>`). Titre, site et **date viennent des métadonnées de la page**, jamais du LLM.
2. **analyze** : domaines détectés de façon déterministe (mots-clés des règles métiers), puis synthèse structurée guidée par le skill [`review-actu`](.claude/skills/review-actu/SKILL.md), les critères du skill `veille-tech`, les **règles métiers numérotées** du domaine, le glossaire et la mémoire de veille.
3. **guard** : chaque affirmation doit **citer un passage présent dans la page** — sinon elle est marquée *non étayée* et la confiance est plafonnée ; les règles sont citées par numéro ; toute URL étrangère à l'article est retirée.
4. **save** : record validé par Pydantic, table SQLite `reviews` (migration v5).

Le **challenge** se fait dans un chat dédié : l'assistant répond à partir des extraits de l'article les
plus proches de la question et de la base de connaissances, reconnaît une erreur de la review quand le
texte la contredit, sinon la défend en citant [1]. **♻️ Réviser** relance le Reviewer avec les objections du débat.

### 📚 Base de connaissances

Fichiers Markdown dans [`knowledge/`](knowledge/README.md) (dépôt, lecture seule) et `data/knowledge/` (téléversés, hors Git) :

- `glossaire.md` — 78 définitions IA / GenAI / LLM, chacune avec domaine, alias, mots-clés et **source primaire vérifiée** (arXiv, documentation, dépôts) ;
- `regles-metiers.md` — règles par domaine (LLM, Inférence, RAG, Agents, Évaluation, Sécurité, Réglementation…) appliquées par le Reviewer ;
- `prompts-veille.md` — prompts **cliquables** (chat de veille ou challenge d'une review ; `{sujet}` à compléter).

L'**index des mots-clés** est généré à partir des termes, alias et mots-clés. Le chat répond aux
« c'est quoi… », « définition de… », « règles métiers pour… » à partir de cette base. Format d'un fichier :

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

Les fichiers téléversés (web, TUI ou `bin/veille knowledge --add`) sont **validés avant écriture**
(format, URLs des sources, 200 ko max) ; les remplacer ou les supprimer demande une confirmation.

### 🧭 Sources par URL

`bin/veille source add https://blog.vllm.ai` (ou l'onglet 🧭 Sources) automatise le skill
[`ajout-source`](.claude/skills/ajout-source/SKILL.md) :

| URL collée | Détection | Vérification |
|---|---|---|
| `github.com/owner/repo` | dépôt GitHub | releases publiées via l'API GitHub |
| `arxiv.org/list/cs.LG` | catégorie arXiv | flux d'annonces `rss.arxiv.org` |
| flux RSS/Atom | flux direct | entrées datées |
| page de blog | `<link rel="alternate">`, puis `/feed`, `/rss.xml`… | entrées datées |

Refus automatiques : presse et agrégateurs (sources primaires uniquement), flux sans entrée datée, dépôt
sans release, adresse non publique. Un aperçu (nom, nombre d'entrées, dernière entrée) est montré avant
confirmation ; `sources.toml` est modifié **en préservant commentaires et mise en forme**, puis relu par
`tomllib` avant d'être remplacé.

### 🗞️ Actus en cartes

Onglet **🗞️ Actus** : grille de cartes carrées (illustration et couleur d'origine pour le blog Claude,
pictogramme par catégorie sinon), filtres par source, recherche, vue cartes ou liste.

| Source | Collecte |
|---|---|
| `[[blog]]` de `sources.toml` (`claude.com/blog`) | page de liste crawlée en HTML (grille + bandeau « à la une »), résumé et image lus dans les balises meta de chaque article ; aussi collectée par la veille (`collect:rss`) |
| `[news].rss` (`OpenAI News`) | flux RSS officiel (`openai.com/fr-FR/news/` répond HTTP 403 aux robots) |
| **🌐 Mes actus via Claude** (barre latérale) | API Claude (`NEWS_MODEL`) + outil serveur `web_search` sur les mots-clés du profil Grill-me ou des sujets saisis |

Pour la recherche web, Claude ne renvoie que des URL ; **titre, URL et date sont recopiés des résultats
`web_search`**, et une URL absente des résultats (ou hors période, ou exclue par le profil) est écartée.
Tout est validé par Pydantic (`NewsItem`) avant la table `news` (migration v6).

```bash
bin/veille news crawl                          # claude.com/blog + OpenAI News
bin/veille news search "Qwen, vLLM" --days 7   # clé CLAUDE_API dans .env
bin/veille news list -s "Claude Blog"
```

### ✨ Interface : Scouty, notifications, modales, assistant

- **Scouty**, petite créature animée (SVG + CSS, respecte `prefers-reduced-motion`), accompagne les
  attentes : chat, recherche, review, crawl, recherche web et défilement des actus.
- **Notifications** : toasts empilés avec barre de vie et action (« Voir les actus », « Ouvrir la page
  Notion »…), centre 🔔 (historique local au navigateur), notification système si l'onglet est en
  arrière-plan (permission demandée au premier crawl ou à la première recherche).
- **Défilement infini** des actus : pages suivantes en base, puis, au bout, pages plus anciennes des
  blogs (`?<id>_page=N` de la liste Webflow) et suite des flux officiels (`POST /api/news/older`).
- **Chat** : réponses en carte, longues réponses repliées (« Voir plus »), bouton copier, sources en
  **cartes ou liste** ; un clic sur une source ou sur une citation `[n]` ouvre une **modale** avec ce que
  la veille a collecté et résumé (résumé, pourquoi c'est important, affirmations, contenu collecté,
  review éventuelle), le lien vers l'article et les actions citer / review. Même vue dans 🔎 Recherche.
- **Review → Notion** : bouton « 📝 Publier dans Notion » (deux clics : confirmation) ; la review est
  ajoutée à la page de veille active via le serveur MCP (seule page que la veille peut compléter), puis
  reprise dans une section « 🔬 Reviews d'actualités » à chaque reconstruction de la page.
- **Assistant Claude** (bouton ✳ en bas à droite) : panneau repliable qui appelle **directement l'API
  Claude** (`ASSISTANT_MODEL`, clé `CLAUDE_API`) en streaming, avec les dernières actus en contexte ;
  🌐 active l'outil `web_search` et affiche les pages citées.

### 📝 Page Notion

`bin/veille notion` (ou « Mets à jour la page Notion » dans le chat) publie via le serveur MCP
`notion-veille`, sous la page parente configurée uniquement :

- **en-tête** : couverture (image de la première ressource), encart d'introduction, sommaire ;
- **dernière veille, développée** : titre, résumé, description chiffrée (signaux retenus / écartés, sources), mots-clés, puis une **fiche par ressource** — titre lié, image d'aperçu (`og:image` de la page source), résumé, « 💡 pourquoi c'est important », métadonnées ;
- **veilles précédentes** : sections **repliables** (résumé, description, liens, mots-clés).

L'ancienne page est archivée (récupérable dans la corbeille Notion). `doctor` vérifie l'accès : si
l'intégration ne voit pas la page parente, il indique quoi faire (••• → Connexions → ajouter l'intégration).

### 🖥️ TUI (OpenTUI + React)

`bin/veille tui` — 10 écrans, dans un terminal, avec spinners, barres de progression et emoji :

| Touche | Écran | Touche | Écran |
|---|---|---|---|
| `1` | 🏠 Accueil : services, mémoire, dernier rapport, raccourcis | `6` | 📚 Knowledge : glossaire, règles, prompts, notes, index |
| `2` | 💬 Chat : streaming, outils, sources `[n]`, parcours du graphe | `7` | 🧭 Sources : ajout par URL vérifiée, retrait, santé |
| `3` | 🔎 Recherche plein texte → chat ou veille ciblée | `8` | 📰 Rapports datés |
| `4` | 🔬 Review : étapes en direct, scores, citations, challenge, révision | `9` | 🧠 Mémoire + publication Notion |
| `5` | 🛰️ Veille : étapes, journal, digest, publier / rejeter | `0` | 🎯 Grill-me |

Navigation : chiffres, `←` `→`, `F1`–`F10` (partout), `Échap` (navigation) / `i` ou `/` (saisie),
`Tab` (panneau), `?` (aide), `q` (quitter). La TUI parle à l'API du serveur web (`VEILLE_URL`, sinon
l'URL de `bin/veille start`) et le **démarre automatiquement** s'il ne répond pas.
Code : [`tui/`](tui/) — `bun test` (tests de la logique et des écrans rendus par le moteur de test d'OpenTUI).

### 🎯 Grill-me, 🧠 mémoire, ✏️ prompts, 🧭 traces

- **Grill-me** : une question à la fois, avec recommandation ; profil (priorités, mots-clés, exclusions) enregistré dans le Store LangGraph et réinjecté dans les prompts.
- **Mémoire** : les notes de validation deviennent des leçons ; export vers `.claude/memory/veille.md` pour que chaque session Claude Code démarre avec le contexte.
- **Prompts éditables** depuis le web (surcharges historisées dans `data/prompts/`, jamais les fichiers du dépôt) : Scout, Critic, Editor, routeur, Grill-me, **Reviewer**, prompt système du chat.
- **Traces** : chaque réponse du chat, chaque veille et chaque review a son parcours dans le graphe (`/trace/<id>`, diagramme Mermaid des nœuds traversés) ; liens Langfuse déterministes.

---

## 🏗️ Architecture

```mermaid
flowchart TB
    subgraph Clients
        WEB[🌐 Interface web<br/>app/web/static]
        TUI[🖥️ TUI OpenTUI + React<br/>tui/]
        CLI[⌨️ bin/veille · app.main]
    end
    subgraph Serveur["FastAPI — app/web/server.py"]
        CHAT[💬 Agent de chat<br/>route → act → respond → guard]
        RUNS[🛰️ Veilles<br/>Supervisor + Task Graph]
        REV[🔬 Reviewer]
        KB[📚 Knowledge]
        SRC[🧭 Sources]
    end
    subgraph Données
        SQL[(SQLite watch.db<br/>documents · digests · reviews · traces)]
        STORE[(Store LangGraph<br/>mémoire)]
        MD[/knowledge/*.md · sources.toml/]
    end
    subgraph Extérieur
        LLM[Ollama / Claude]
        MCPG[MCP github-scout]
        MCPN[MCP notion-veille]
    end
    WEB & TUI --> Serveur
    CLI --> RUNS & REV & KB & SRC
    CHAT & RUNS & REV --> LLM
    RUNS --> MCPG
    RUNS & CHAT --> MCPN
    Serveur --> SQL & STORE & MD
```

```text
app/
├── workflow/        Supervisor, Task Graph, sous-agents (Scout, Critic, Editor)
├── chat/            agent de chat + outils (recherche, digests, GitHub, Notion, knowledge, challenge)
├── review.py        agent Reviewer (URL → review vérifiée)
├── knowledge.py     base de connaissances Markdown (glossaire, index, règles, prompts, uploads)
├── sources_admin.py sources par URL (détection, vérification, écriture de sources.toml)
├── notion.py        page Notion (mise en forme, images) via MCP
├── harness/         guards (MCP, injections, contrats d'état), hooks de publication, skills, prompts
├── mcp_servers/     serveurs MCP github-scout et notion-veille
├── web/             serveur FastAPI + interface web
└── storage.py       SQLite + migrations (v1 → v5)
tui/                 TUI OpenTUI + React (Bun), tests et script de captures
knowledge/           glossaire, règles métiers, prompts (Markdown)
.claude/             skills, sous-agents, hooks et mémoire pour Claude Code
docs/                documentation, captures d'écran
```

---

## ⚙️ Configuration

Tout se règle dans `.env` (modèle : [.env.example](.env.example)) — jamais versionné.

| Variable | Rôle | Défaut |
|---|---|---|
| `LLM_PROVIDER` · `LLM_MODEL` · `LLM_BASE_URL` | `ollama` \| `openai` \| `anthropic`, modèle, URL | `ollama` · `gemma-3-4b-it` |
| `ANTHROPIC_API_KEY` | clé API Claude (si `anthropic`) | — |
| `CLAUDE_API` · `NEWS_MODEL` · `NEWS_SEARCH_TOOL` · `NEWS_SEARCH_MAX_USES` | recherche web des actus (clé, modèle, outil, recherches par appel) | — · `claude-sonnet-5` · `web_search_20250305` · 5 |
| `ASSISTANT_MODEL` · `CLAUDE_WORKSPACE_ID` | assistant Claude flottant ; workspace si la clé n'y est pas rattachée | `claude-sonnet-5` · — |
| `MAX_LLM_CALLS` · `MAX_DOCUMENTS_PER_RUN` · `MAX_AGE_DAYS` | budgets d'une veille | 20 · 24 · 14 |
| `HUMAN_APPROVAL` | arrêt avant publication | `true` |
| `GITHUB_TOKEN` | quota API GitHub (collecte, sources) | — |
| `NOTION_TOKEN` · `NOTION_PARENT_PAGE_ID` · `NOTION_DIGESTS` | publication Notion | — · — · 10 |
| `KNOWLEDGE_UPLOADS_DIR` | fichiers Markdown téléversés | `data/knowledge` |
| `REVIEW_MAX_CHARS` · `REVIEW_TIMEOUT` · `REVIEW_ALLOW_PRIVATE` | review par URL | 12000 · 20 · `false` |
| `WEB_HOST` · `WEB_PORT` | serveur web | `127.0.0.1` · 8000 |
| `LANGFUSE_*` · `LANGSMITH_*` | observabilité (facultatif) | désactivée |

Lanceur : `VEILLE_PORT`, `VEILLE_HOST`, `VEILLE_RUN_DIR`. TUI : `VEILLE_URL`, `VEILLE_SCREEN`, `VEILLE_NO_START`.

---

## 🛡️ Sécurité et règles du harness

- **Rien d'inventé** : dates, versions, URL et chiffres viennent des sources ; les guards retirent les URL non sourcées et les citations hors plage ; la review vérifie chaque citation dans la page.
- **Contenus non fiables** : les injections de prompt connues sont neutralisées avant d'entrer dans un prompt (`sanitize_untrusted`).
- **SSRF** : review et ajout de sources refusent les adresses non publiques, y compris après redirection.
- **MCP** : GitHub en lecture seule (liste blanche d'outils, arguments bornés) ; Notion limité à la page parente configurée et aux pages créées par la veille.
- **Secrets** : `.env` jamais versionné ; hooks Claude Code bloquant les secrets probables, `rm -rf` et l'affichage de `.env`.
- **Schéma SQLite** : toute modification = une migration + un test.

Règles complètes : [CLAUDE.md](CLAUDE.md).

---

## 🧪 Tests

```bash
.venv/bin/python -m pytest -q          # tests Python : unitaires + E2E (faux LLM, faux GitHub, faux Notion)
cd tui && ./node_modules/.bin/bun test  # tests TUI : logique + écrans rendus
RUN_LIVE=1 .venv/bin/python -m pytest -q tests/e2e/test_live.py -s   # E2E réel (Ollama + réseau)
```

Captures d'écran de la TUI : `VEILLE_URL=http://127.0.0.1:8000 tui/node_modules/.bin/bun run tui/scripts/snapshot.tsx`
puis `tui/scripts/png.sh` (Chromium).

---

## 📖 Documentation

- [Getting started](docs/getting-started.html) — Ollama, installation, Notion, Langfuse, LangSmith
- [Documentation complète](docs/index.html)
- [LangGraph dans le projet](docs/langgraph.md)
- [LLM : Ollama, API Claude](docs/llm-ollama-claude.md)
- [Mise à jour et idées](docs/UPGRADE.md) · [Jetson Orin 8 Go](docs/jetson-orin.md)
- [Base de connaissances : format](knowledge/README.md)
