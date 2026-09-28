---
name: schema-mermaid
description: Produire ou mettre à jour un schéma Mermaid fidèle au code (graphe LangGraph, Task Graph, séquence du chat, schéma SQLite, architecture, flux de publication) et le valider avec le CLI officiel mermaid-cli avant de l'insérer dans la doc. À utiliser pour « fais un schéma de… », « diagramme », « mets à jour le graphe de la doc », ou quand un changement de app/workflow/, app/storage.py ou app/chat/ rend un diagramme obsolète.
---

# Principe

Un schéma de la doc est une **affirmation sur le code** : comme une actu de la veille, chaque nœud, flèche
et table doit se retrouver dans une source (fichier + ligne, ou sortie d'une commande du projet).
Ne jamais dessiner un composant, une table ou un appel qui n'existe pas.

Rendu : les blocs ```` ```mermaid ```` s'affichent tels quels dans GitHub, VS Code et Notion ; la page
`/trace/<id>` les rend avec mermaid.js. On versionne donc le **texte Mermaid**, pas des images
(sauf demande explicite de SVG avec `--out`).

# Sources de vérité par type de schéma

| Schéma | Type Mermaid | Source (ne pas redessiner à la main si elle existe) |
|:--|:--|:--|
| Graphe LangGraph compilé | `graph TD` | `.venv/bin/python -m app.main plan --langgraph --out <dossier>` → `langgraph.mmd` |
| Task Graph (DAG des tâches) | `flowchart LR` | même commande → `task-graph.mmd` (`TaskGraph.to_mermaid`, `app/workflow/tasks.py`) |
| Sous-graphes quality / evidence / sous-agents | `flowchart LR` | `app/workflow/quality.py`, `evidence.py`, `subagents.py` (lire les `add_node` / `add_edge`) |
| Agent de chat (route → act → respond → guard) | `flowchart` ou `sequenceDiagram` | `app/chat/` |
| Schéma SQLite | `erDiagram` | `MIGRATIONS` dans `app/storage.py` (état final après toutes les migrations) |
| Collecte → publication | `flowchart LR` | `app/collectors/`, `app/publishers.py`, `app/notion.py` |
| Cron | `sequenceDiagram` ou `stateDiagram-v2` | `app/scheduler.py` |

Les diagrammes générés sont recopiés **sans retouche du graphe** ; seuls le titre et un regroupement en
`subgraph` sont permis, à signaler dans la légende.

# Règles de style

- **Lisible avant tout** : 15 nœuds au plus par diagramme ; au-delà, découper (vue d'ensemble + détail).
- Libellés **en français**, courts ; identifiants de nœuds = noms du code (`research`, `evidence`), sans espace.
- Direction : `LR` pour un pipeline, `TD` pour une hiérarchie ou un graphe à superviseur.
- Formes cohérentes : `[tâche]`, `([début/fin])`, `{décision}`, `[(table SQLite)]`, `[[sous-graphe/agent]]`,
  `>entrée externe]`. Arête pleine = toujours ; pointillée `-.->` = conditionnelle (comme LangGraph).
- Couleurs : uniquement par `classDef` réutilisés (pas de `style` nœud par nœud) ; palette sobre lisible en
  clair et en sombre, par exemple :
  ```
  classDef llm fill:#ede7f6,stroke:#5e35b1,color:#1a1a1a
  classDef det fill:#e8f5e9,stroke:#2e7d32,color:#1a1a1a
  classDef ext fill:#fff3e0,stroke:#ef6c00,color:#1a1a1a
  classDef guard fill:#fde2e1,stroke:#c62828,color:#1a1a1a
  ```
  (`llm` = nœud qui appelle le modèle, `det` = déterministe, `ext` = service externe/MCP, `guard` = garde-fou.)
- Pas de HTML dans les libellés sauf `<br/>` ; guillemets doubles autour d'un libellé contenant `( ) : / #`.
- Pas de `%%{init}%%` ni de thème imposé : GitHub et `/trace` choisissent clair/sombre.
- Toujours une **légende d'une ligne** sous le bloc : source (commande ou fichier) et date de génération.

# Procédure

1. Identifier le type de schéma et sa source dans le tableau ; lire le code concerné.
2. Générer (`plan --langgraph --out`) ou rédiger le texte Mermaid dans un fichier temporaire `.mmd`
   du scratchpad (jamais dans `data/` ni `output/`).
3. **Valider** : `.claude/skills/schema-mermaid/scripts/check.sh <fichier.mmd>` (CLI officiel
   `@mermaid-js/mermaid-cli`, version épinglée). Corriger jusqu'à `✓`. Ne jamais insérer un diagramme non validé.
4. Insérer le bloc dans la doc cible (Markdown : bloc ```` ```mermaid ```` ; `docs/*.html` : bloc
   `<pre><code>` dans un `<details>` comme dans `docs/index.html`, en échappant `<` et `>`), puis revalider
   le fichier Markdown entier : `check.sh docs/langgraph.md`.
5. Rendre à l'utilisateur : le diagramme, la liste des sources (fichier:ligne ou commande) et ce qui a changé
   par rapport à l'ancien schéma (nœuds ajoutés / retirés).

# Garde-fous

- Pas de chiffres (latence, jetons/s, mémoire) dans un schéma sans mesure citée.
- Ne pas modifier le code pour « simplifier » un schéma ; c'est le schéma qui suit le code.
- Rendu SVG (`check.sh --out docs/diagrams …`) seulement sur demande : mmdc pilote Chromium via Puppeteer,
  outil du **poste de dev** ; il n'est pas une dépendance de l'application ni de la Jetson.
