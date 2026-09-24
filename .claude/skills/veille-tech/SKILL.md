---
name: veille-tech
description: Collecter, vérifier et synthétiser une veille LLM/GenAI sourcée.
---

# Procédure

1. Collecter RSS, arXiv et GitHub Releases.
2. Dédupliquer par URL, titre normalisé et similarité sémantique.
3. Classer les sources primaires avant les reprises de presse.
4. Noter chaque signal : pertinence, nouveauté, confiance.
5. Vérifier date, version, URL et attribution.
6. Rejeter toute affirmation sans source.
7. Générer un digest Markdown + JSON strictement conforme aux modèles.
8. Demander validation humaine si `HUMAN_APPROVAL=true`.

# Exécution

1. `.venv/bin/python -m app.main doctor` — tout doit être OK (Ollama, modèle, MCP).
2. `.venv/bin/python -m app.main run` — le supervisor exécute le Task Graph :
   collecte parallèle (RSS, arXiv, releases, MCP GitHub) → prefilter →
   research → review → editorial → validation humaine.
3. Lire les lignes `supervisor …` : relance de research, réparation, blocage.
4. Auditer avec le skill `audit-digest`, puis publier ou rejeter :
   `.venv/bin/python -m app.main resume <run_id> --approved|--rejected --note "..."`.
5. `.venv/bin/python -m app.main memory --export` pour rafraîchir la mémoire
   Claude Code (`.claude/memory/veille.md`).

# Critères de priorité

- LLM open source et capacités réelles.
- Inférence GPU, quantification, vLLM, TensorRT-LLM, llama.cpp, edge.
- Agents, MCP, RAG, évaluation et observabilité.
- Évolutions Hugging Face, NVIDIA, LangGraph, Ollama, PyTorch.
- Nouveaux benchmarks avec méthodologie disponible.
