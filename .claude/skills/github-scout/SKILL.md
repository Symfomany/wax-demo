---
name: github-scout
description: Découvrir des dépôts GitHub open source récents et pertinents pour la veille LLM/GenAI via le serveur MCP github-scout (lecture seule). À utiliser pour « quels nouveaux repos LLM / agents / RAG cette semaine ? » ou pour enrichir sources.toml.
---

# Procédure

1. Construire des requêtes datées, jamais de date inventée : calculer
   `created:>AAAA-MM-JJ` à partir de la date du jour (`date -I -d '21 days ago'`).
2. Appeler l'outil MCP `search_repositories` (perPage ≤ 10) ; une requête par thème :
   inférence, agents/MCP, RAG, évaluation.
3. Pour les 3 à 5 dépôts les plus étoilés, lire `get_readme` et, si utile,
   `list_releases`.
4. Écarter : tutoriels et listes « awesome », dépôts sans licence pour un usage
   pro, outils offensifs, dépôts vides ou sans README.
5. Restituer un tableau : dépôt (URL), étoiles, langage, licence, date de
   création, une phrase fondée sur le README uniquement.
6. Pour un suivi durable, proposer l'ajout de la requête dans `[github_mcp]` de
   `sources.toml` (skill `ajout-source`), ou du dépôt dans `[github].repositories`
   pour suivre ses releases.

# Règles

- Lecture seule : aucun outil MCP d'écriture (le hook `guard.py` les bloque).
- Les chiffres (étoiles, dates) viennent uniquement des réponses de l'API.
- Équivalent CLI : `python -m app.main github "llm inference" --limit 5`.
