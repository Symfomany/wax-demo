---
name: ajout-source
description: Ajouter ou retirer une source de veille (flux RSS, flux arXiv, dépôt GitHub, requête MCP) dans sources.toml après vérification. À utiliser dès qu'on modifie la liste des sources.
---

# Procédure

1. Vérifier que la source est **primaire** (blog officiel, release notes, arXiv,
   dépôt de l'éditeur). Refuser les agrégateurs et la presse.
2. Vérifier qu'elle répond et contient des entrées datées :
   ```bash
   .venv/bin/python -c "import feedparser as f; d=f.parse('URL'); print(d.get('status'), len(d.entries), d.entries[0].get('link'), d.entries[0].get('published'))"
   ```
   Pour un dépôt : `mcp__github-scout__list_releases` doit renvoyer des releases.
3. Ne jamais ajouter une URL non vérifiée à l'étape 2 (CLAUDE.md : ne rien inventer).
4. Éditer `sources.toml` dans la bonne section (`[[rss]]`, `[arxiv].feeds`,
   `[github].repositories`, `[github_mcp].queries`).
5. Valider : `.venv/bin/python -m app.main doctor` puis
   `.venv/bin/python -m pytest -q`.
6. Pour une source retirée, consulter la santé des sources
   (`python -m app.main memory`) et citer le dernier échec dans le message.
