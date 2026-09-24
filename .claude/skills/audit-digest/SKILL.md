---
name: audit-digest
description: Auditer un digest en attente de validation ou publié (sources, URLs, dates, affirmations) avant de décider resume --approved ou --rejected. À utiliser quand un run est en attente de validation humaine.
---

# Procédure

1. Identifier le run en attente : le hook SessionStart l'affiche ; sinon
   `sqlite3 data/watch.db "SELECT run_id FROM runs WHERE status='awaiting_approval'"`.
2. Déléguer la vérification au sous-agent `source-auditor` (URLs, dates,
   chiffres, doublons) et, si le code a changé, au sous-agent `schema-auditor`.
3. Décider :
   - tout est traçable → `python -m app.main resume <run_id> --approved --note "<leçon>"` ;
   - sinon → `python -m app.main resume <run_id> --rejected --note "<motif précis>"`.
4. La note devient une leçon du Store LangGraph, réinjectée dans les prompts
   Scout/Editor et exportée dans `.claude/memory/veille.md`. Rédiger une
   consigne réutilisable (« Écarter les tutoriels ») plutôt qu'un constat
   ponctuel (« l'item 3 est faux »).
