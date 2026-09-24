# UPGRADE — mise à jour et feuille de route

## 1. Mettre à jour une installation

```bash
cd ~/llm-harness
git pull
source .venv/bin/activate
pip install -e ".[dev]"            # nouvelles dépendances éventuelles
python -m pytest -q                # doit être vert
python -m app.main doctor          # migrations SQLite appliquées automatiquement au démarrage
```

Les migrations SQLite sont versionnées par `PRAGMA user_version` et appliquées à la première
connexion ; elles ne modifient jamais une migration existante (CLAUDE.md). Sauvegarde conseillée
avant une mise à jour majeure : copier `data/` (sans supprimer quoi que ce soit).

### Notes de version

#### v0.5 (cette version)

| Changement | Impact |
|:--|:--|
| **Liens Langfuse** sous chaque réponse, sur les traces et les veilles (identifiant de trace déterministe) | facultatif : `LANGFUSE_PROJECT_ID` évite un appel API |
| **`bin/veille`** : lanceur bash (`cycle`, `run`, `resume`, `approve`, `reject`, `pending`, `show`, `grill`…) | nouvelles commandes `pending`, `show`, `grill`, `grill-save`, option `run --run-id-file` |
| **Grill-me** : entretien sur tes centres d'intérêt (onglet, CLI, chat, skill Claude Code) | profil dans le Store, réinjecté dans les prompts et exporté vers `.claude/memory/veille.md` |

#### v0.4

| Changement | Impact |
|:--|:--|
| Rapports en **HTML** en plus du Markdown (`reports/AAAA/veille-….html`) | `python -m app.main report` régénère les deux formats pour une veille déjà publiée |
| `GET /api/reports/{année}/{nom}` renvoie `markdown` + `html_url` (plus de champ `html`) | le HTML est servi par `/reports/…` (CSP sans scripts), affiché dans une iframe sandboxée |
| **Migration SQLite v4** : table `traces` | automatique |
| **Prompts éditables** : surcharges dans `PROMPT_OVERRIDES_DIR` (`data/prompts/`) | les fichiers du dépôt ne changent pas ; « Revenir au prompt par défaut » supprime la surcharge |
| **`LLM_PROVIDER`** : `ollama` / `openai` / `anthropic` | défaut `ollama`, comportement inchangé |
| Recherche filtrée `POST /api/search`, veille ciblée (`keywords`, `sources`, `max_age_days`, `max_documents`) | nouvelles options de `run` : `-k`, `-s`, `--max-age`, `--max-docs`, `--match-all` |
| Dépendances : `anthropic`, `langchain-anthropic` | `pip install -e .` |

#### v0.3
Interface web de chat, rapports Markdown datés, MCP Notion, instructions templatées, migration v3
(conversations, pages Notion, recherche FTS5).

---

## 2. Idées de fonctionnalités

Classées par rapport valeur / effort. « Effort » : S (≤ ½ jour), M (1–3 jours), L (> 3 jours).

### Qualité de la veille

| Idée | Effort | Pourquoi |
|:--|:--:|:--|
| **Second avis Claude** sur les signaux `needs_review` uniquement (routage multi-modèles : Scout local, Critic Claude) | S | qualité là où le petit modèle doute, coût limité |
| **Déduplication sémantique** (embeddings Ollama, ex. modèle d'embedding local) + regroupement des annonces identiques | M | une même release relayée par plusieurs sources compte une fois |
| **Évaluation automatique** : jeu de référence de digests validés, LLM-juge, score par version de prompt (datasets Langfuse / LangSmith) | M | mesurer l'effet d'une modification de prompt au lieu de l'estimer |
| **A/B de prompts** : deux surcharges en parallèle, comparaison des taux d'acceptation humaine | M | s'appuie sur l'édition de prompts et les traces |
| **Retour par item** (👍 / 👎 sur chaque signal) → mémoire plus fine que la note globale | S | leçons plus précises |
| Transformer automatiquement une leçon répétée en **règle déterministe** proposée (ex. `exclude_keywords`) | M | automatise le cycle « leçon → règle » |

### Sources

| Idée | Effort | Pourquoi |
|:--|:--:|:--|
| Nouveaux modèles publiés sur Hugging Face (API du Hub) | S | signal primaire sur les modèles ouverts |
| Nouveautés de la bibliothèque Ollama | S | pertinent pour l'inférence locale |
| Releases des dépôts découverts par MCP, ajoutés d'un clic à `[github].repositories` | S | du « découvert » au « suivi » |
| Profils de veille multiples (`profil.toml` par thème : inférence, agents, sécurité…) | M | une veille par sujet |

### Restitution

| Idée | Effort | Pourquoi |
|:--|:--:|:--|
| **Synthèse hebdomadaire / mensuelle** agrégeant les digests + tendances des mots-clés dans le temps | M | vision d'ensemble |
| Flux RSS / Atom du digest lui-même | S | lecture dans un agrégateur |
| Export PDF du rapport HTML (feuille d'impression déjà prévue) | S | diffusion |
| Notifications (Slack, Teams, courriel) à la publication, via un publisher de plus | S | l'architecture `publishers.py` le prévoit |
| **Serveur MCP exposant la veille** (`search_watch`, `latest_digests`) pour Claude Desktop / Claude Code | S | interroger sa veille depuis n'importe quel client MCP |

### Plateforme

| Idée | Effort | Pourquoi |
|:--|:--:|:--|
| Progression des veilles en **SSE** plutôt qu'en interrogation périodique | S | interface plus réactive |
| Authentification de l'interface (avant toute exposition hors `127.0.0.1`) | M | sécurité |
| Tableau de bord coûts / jetons / latences par agent (depuis les traces) | M | pilotage, surtout avec l'API Claude |
| File de veilles planifiées dans l'interface (au lieu du timer systemd) | M | confort |
| Déploiement **edge** sur Jetson Orin 8 Go | M | voir `docs/jetson-orin.md` |
