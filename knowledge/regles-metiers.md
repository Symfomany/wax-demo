---
title: Règles métiers par domaine IA
type: regles
---

# Règles métiers par domaine IA

Grille de lecture appliquée par l'agent **Review** et par l'humain qui valide la veille.
Chaque section `## Domaine` porte des `Mots-clés :` qui servent à reconnaître le domaine
d'un article ; chaque puce est une règle vérifiable. Les règles **Transverse** s'appliquent
à tous les articles.

## Transverse
Domaine : Transverse
Mots-clés : annonce, article, actualité

- Toute affirmation retenue doit pouvoir être rattachée à un passage de la source ; sinon elle est « non étayée ».
- Préférer la source primaire (release notes, article arXiv, dépôt, blog officiel, documentation) à une reprise de presse.
- Une date, une version, un chiffre ou un nom de projet absent de la source ne doit jamais être complété de mémoire.
- Distinguer l'annonce (disponible, téléchargeable, documentée) de l'intention (« bientôt », « prévu », « en preview »).
- Signaler le ton marketing (superlatifs, comparaisons sans protocole, « révolutionnaire ») comme risque.
- Écarter les cours, tutoriels et feuilles de route d'apprentissage ; privilégier outils, modèles et releases.

## LLM
Domaine : LLM
Mots-clés : llm, language model, modèle de langage, model, modèle, weights, poids, parameters, paramètres, context window, reasoning, raisonnement, instruct, chat model, open-weights, release

- Un nouveau modèle ne compte que s'il est accessible (poids, API ou produit) ou décrit par un rapport technique.
- Relever la taille (paramètres totaux et actifs pour un MoE), la fenêtre de contexte et la licence si la source les donne.
- Distinguer poids ouverts, open source (code + données) et accès API seul.
- Une comparaison à d'autres modèles n'est recevable que si les benchmarks, versions et protocoles sont nommés.
- Vérifier l'existence d'une model card ou d'un rapport technique ; son absence est un risque.

## Inférence et déploiement
Domaine : Inférence
Mots-clés : inference, inférence, serving, latency, latence, throughput, débit, quantization, quantification, fp8, int4, gguf, vllm, tensorrt, llama.cpp, ollama, gpu, kernel, kv cache, edge, jetson, on-device

- Un gain de performance n'est recevable qu'avec le matériel, la précision, la taille de lot et les longueurs d'entrée/sortie.
- Une quantification doit être accompagnée d'une mesure de perte de qualité (benchmark ou perplexité).
- Préciser la version du moteur (vLLM, TensorRT-LLM, llama.cpp…) et si la fonctionnalité est stable ou expérimentale.
- Distinguer latence (TTFT, par requête) et débit (agrégé) : ne pas les confondre.

## Entraînement et fine-tuning
Domaine : Entraînement
Mots-clés : training, entraînement, fine-tuning, finetuning, lora, qlora, rlhf, dpo, alignment, alignement, pretraining, pré-entraînement, dataset, données, distillation, synthetic data

- Relever les données (origine, taille, licence) et le budget de calcul s'ils sont publiés.
- Une méthode nouvelle doit être comparée à une base de référence explicite.
- Signaler l'absence de code ou de recette reproductible.
- Les jeux de données publiés : vérifier la licence et la présence d'une fiche (dataset card).

## RAG et recherche
Domaine : RAG
Mots-clés : rag, retrieval, recherche, embedding, embeddings, vector, vectorielle, reranker, reranking, chunking, graphrag, knowledge base, base de connaissances, search, bm25

- Une amélioration de RAG doit préciser le jeu d'évaluation, la métrique (rappel, exactitude, fidélité) et la base comparée.
- Distinguer le gain de la recherche (retrieval) de celui de la génération.
- Un modèle d'embedding : relever dimension, langues couvertes, taille de contexte et licence si disponibles.

## Agents et outils
Domaine : Agents
Mots-clés : agent, agents, agentic, mcp, model context protocol, tool calling, function calling, tool use, a2a, multi-agent, orchestration, langgraph, workflow, computer use, browser

- Préciser le niveau d'autonomie, les outils accessibles et les garde-fous (validation humaine, permissions).
- Une annonce de protocole (MCP, A2A…) : relever la version de la spécification et les implémentations de référence.
- Un score d'agent doit nommer le benchmark, la variante et le harnais (échafaudage) utilisé.
- Signaler les risques de sécurité : injection indirecte, exfiltration, actions irréversibles.

## Évaluation et benchmarks
Domaine : Évaluation
Mots-clés : benchmark, evaluation, évaluation, eval, leaderboard, score, mmlu, swe-bench, humaneval, arena, elo, llm-as-a-judge, contamination

- Un score n'est recevable qu'avec la version du benchmark, le protocole (shots, CoT, nombre d'essais) et la date.
- Un nouveau benchmark doit publier sa méthodologie et ses données ou son code.
- Signaler les risques de contamination et de saturation.
- Un classement par préférences humaines (Arena) n'est pas une mesure de capacité sur une tâche précise.

## Sécurité et robustesse
Domaine : Sécurité
Mots-clés : security, sécurité, safety, jailbreak, prompt injection, injection, red teaming, vulnerability, vulnérabilité, cve, guardrails, alignment, misuse

- Une vulnérabilité doit citer un identifiant (CVE, avis de sécurité) ou un dépôt de preuve, et les versions affectées.
- Distinguer démonstration de recherche et exploitation constatée.
- Ne jamais reproduire de charge d'attaque exploitable dans la synthèse.
- Relever le correctif ou la mitigation disponibles.

## Multimodal (image, audio, vidéo)
Domaine : Multimodal
Mots-clés : multimodal, vision, image, video, vidéo, audio, speech, voix, tts, asr, diffusion, text-to-image, text-to-video, vlm

- Préciser les modalités en entrée et en sortie, la résolution ou durée maximale et la licence.
- Les exemples choisis par l'éditeur ne suffisent pas : chercher une évaluation quantitative.
- Relever les dispositifs de marquage ou de provenance des contenus générés s'ils sont mentionnés.

## Robotique et IA incarnée
Domaine : Robotique
Mots-clés : robot, robotics, robotique, humanoid, humanoïde, embodied, vla, manipulation, locomotion, simulation

- Distinguer résultats en simulation et sur robot réel.
- Relever le matériel, le nombre de tâches et le taux de réussite avec le protocole.
- Une vidéo de démonstration n'est pas une évaluation : noter l'absence de protocole.

## Réglementation et gouvernance
Domaine : Réglementation
Mots-clés : regulation, réglementation, ai act, law, loi, compliance, conformité, gpai, cnil, commission, governance, gouvernance, copyright, droit d'auteur, policy

- Citer le texte officiel (journal officiel, EUR-Lex, site du régulateur), pas seulement un commentaire.
- Distinguer texte adopté, proposition, lignes directrices et consultation.
- Relever les dates d'entrée en application et le périmètre (qui est concerné).
- Ne jamais présenter une interprétation comme un avis juridique.

## MLOps et observabilité
Domaine : MLOps
Mots-clés : mlops, llmops, observability, observabilité, tracing, langfuse, langsmith, opentelemetry, monitoring, deployment, pipeline, ci

- Relever la compatibilité (fournisseurs, frameworks, standards comme OpenTelemetry).
- Préciser le mode de déploiement (auto-hébergé, SaaS) et la licence.
- Une fonctionnalité en bêta ou en preview doit être signalée comme telle.

## Recherche académique
Domaine : Recherche
Mots-clés : arxiv, paper, article scientifique, preprint, neurips, icml, iclr, acl, study, étude

- Un preprint arXiv n'est pas relu par les pairs : le signaler.
- Relever la disponibilité du code et des données, et la taille des expériences.
- Ne pas généraliser un résultat au-delà des modèles et tâches testés.

## Open source et dépôts
Domaine : Open source
Mots-clés : github, open source, repository, dépôt, repo, release, license, licence, stars, fork

- Relever la licence, la date de la dernière release et l'activité récente du dépôt.
- Les étoiles GitHub mesurent la popularité, pas la qualité : ne pas en faire un argument de fond.
- Une release doit être rattachée à ses notes de version.

## Marché et entreprises
Domaine : Marché
Mots-clés : funding, levée de fonds, acquisition, rachat, partnership, partenariat, pricing, prix, revenue, valuation, valorisation, startup

- Citer le communiqué officiel ou le dépôt réglementaire pour tout montant.
- Distinguer annonce de partenariat et produit disponible.
- Un prix d'API doit préciser l'unité (par million de tokens, entrée/sortie) et la date.
