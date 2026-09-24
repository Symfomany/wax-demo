---
name: grill-me
description: Interroger l'utilisateur sans relâche, une question à la fois, pour cerner ce qu'il cherche dans l'actualité IA (LLM, nouveaux modèles, robotique, agents, architecture, recherche, événements, outils, réglementation), puis enregistrer son profil de veille. À utiliser pour « grill me », « interroge-moi sur ma veille », « aide-moi à définir ce que je veux suivre ».
argument-hint: "[domaine de départ, facultatif]"
---

# Grill-me — cerner ce que tu cherches en actu IA

Point de départ éventuel : `$ARGUMENTS`.

## Règles de l'entretien

1. **Une seule question à la fois.** Attendre la réponse avant la suivante.
2. **Chaque question vient avec ta recommandation** (« Ma recommandation : … parce que … ») et la
   raison pour laquelle tu la poses. Proposer 3 à 6 options numérotées ; accepter le texte libre.
3. **Descendre chaque branche choisie** avant de passer à la suivante ; résoudre les dépendances
   (ex. le matériel détermine les modèles pertinents).
4. **Challenger** :
   - plus de 3 domaines → demander les 3 priorités ;
   - réponse vague (« tout », « je ne sais pas ») → demander un exemple concret de sujet qu'il aurait
     aimé lire cette semaine ;
   - contradiction (ex. « modèles locaux » + « seulement GPT ») → la signaler et trancher.
5. **Ne rien inventer** : aucune URL ; les sources suggérées doivent être vérifiées avec le skill
   `ajout-source` avant tout ajout à `sources.toml`.

## Arbre de questions (même banque que l'application : `app/grill.py`)

1. **Domaines** : LLM · Nouveaux modèles · Robotique & IA incarnée · Agents & MCP · Architecture & infra ·
   Recherche (arXiv) · Événements & conférences · Outils & frameworks · Réglementation & société.
2. **Par domaine choisi** :
   - LLM : ouverts / propriétaires / petits modèles locaux / multimodal / raisonnement ; critères
     (benchmarks avec méthodologie, licence, contexte long, coût).
   - Nouveaux modèles : familles (Llama, Qwen, Gemma, Mistral, DeepSeek, Claude, GPT, Phi) ; seuil
     (majeures seulement ou toutes les releases).
   - Robotique : humanoïdes, VLA, simulation, manipulation, embarqué (Jetson, ROS) ; recherche ou produits.
   - Agents : MCP, orchestration, agents de code, évaluation, sécurité (injections).
   - Architecture : serveurs d'inférence, quantification, RAG, observabilité, edge ; matériel utilisé.
   - Recherche : catégories arXiv (cs.CL, cs.AI, cs.LG, cs.RO, cs.CV, cs.MA) ; code publié, benchmarks, surveys.
   - Événements : conférences, keynotes éditeurs, meetups, hackathons ; zone géographique.
   - Outils : LangChain/LangGraph, LlamaIndex, Ollama/llama.cpp, assistants de code, Langfuse/LangSmith.
   - Réglementation : AI Act, droit d'auteur et données, sécurité et alignement.
3. **Communes** : exclusions · niveau de détail · rythme · mots-clés précis.

## Clôture

1. Résumer le profil en 3 phrases et le faire valider.
2. L'enregistrer (mémoire des agents + `.claude/memory/veille.md`) :
   ```bash
   .venv/bin/python -m app.main grill-save '{"domains": [...], "priorities": [...], "keywords": [...],
     "exclusions": [...], "arxiv_categories": [...], "depth": "summary", "frequency": "daily",
     "summary": "..."}'
   ```
   (`suggested_sources` : uniquement des URLs vérifiées.)
3. Proposer une veille ciblée : `bin/veille cycle -k <mot-clé> -k <mot-clé>`.

Équivalents sans Claude Code : `bin/veille grill` (terminal) ou l'onglet 🎯 Grill-me de l'interface.
