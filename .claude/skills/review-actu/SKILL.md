---
name: review-actu
description: Faire la review sourcée d'une actualité IA à partir de son URL (scraping, synthèse selon les règles métiers du domaine, affirmations citées), puis la défendre ou la corriger quand l'utilisateur la challenge. À utiliser pour « review cette URL », « analyse cet article », « que vaut cette annonce ».
---

# Méthode

1. Lire uniquement le texte de la page : titre, site et date viennent des métadonnées de la page, jamais de la mémoire.
2. Identifier le type de source : primaire (éditeur, auteurs, dépôt, release notes, texte officiel) ou secondaire (presse, blog tiers, agrégateur).
3. Résumer en 3 à 5 phrases factuelles, sans superlatif repris de l'article.
4. Extraire au plus 8 affirmations clés ; pour chacune, recopier mot pour mot le passage qui l'étaye. Sans passage, l'affirmation est « non étayée ».
5. Appliquer les règles métiers du domaine détecté (base de connaissances `knowledge/regles-metiers.md`) et les règles transverses : chaque règle est respectée, non respectée ou non applicable, avec une note courte.
6. Noter pertinence, nouveauté et confiance (0–10) selon les critères de priorité de la veille (skill `veille-tech`).
7. Lister les risques (marketing, chiffres sans protocole, preview présentée comme disponible…) et les questions qui permettraient de vérifier ou de challenger l'article.

# Challenge

- Répondre à partir des extraits de l'article et de la base de connaissances uniquement.
- Si l'utilisateur a raison (l'extrait contredit ou n'étaye pas la synthèse), le reconnaître et proposer la correction.
- Sinon, défendre la synthèse en citant le passage de l'article.
- « ♻️ Réviser la synthèse » relance le Reviewer avec les objections du débat.

# Exécution

- Interface web : onglet **🔬 Review** (`python -m app.main web`), puis chat de challenge sous la synthèse.
- CLI : `.venv/bin/python -m app.main review <URL>` (`--json` pour la sortie brute).
- Claude Code seul : récupérer la page avec WebFetch, lire `knowledge/regles-metiers.md`, appliquer la méthode ci-dessus et ne citer que l'URL de l'article.
