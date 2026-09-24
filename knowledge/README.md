# Base de connaissances (knowledge)

Fichiers Markdown chargés par `app/knowledge.py` et affichés dans l'onglet **📚 Knowledge**
de l'interface web. Ce README n'est pas chargé.

- `glossaire.md` : définitions IA / GenAI / LLM (index de mots-clés généré à partir des termes, alias et mots-clés) ;
- `regles-metiers.md` : règles métiers par domaine, appliquées par l'agent Review ;
- `prompts-veille.md` : prompts cliquables (chat de veille ou challenge d'une review).

Les fichiers téléversés depuis l'interface vont dans `data/knowledge/` (non suivi par Git) ;
ceux du dépôt ne sont jamais modifiés par l'interface.

## Format

```markdown
---
title: Mon titre
type: glossaire | regles | prompts | note
---

## Terme ou titre d'entrée
Domaine : RAG
Alias : autre nom, sigle
Mots-clés : mot, autre mot
Source : https://url-primaire
Cible : chat | review            (prompts uniquement)

Corps en Markdown. Pour `regles`, chaque puce est une règle.
```

Sans front matter, le fichier est une `note` titrée par son premier `# Titre` (ou son nom).
Les lignes `Clé : valeur` ne sont lues qu'en tête d'entrée ; `Source :` doit être une URL http(s).
