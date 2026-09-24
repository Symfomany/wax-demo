---
title: Glossaire IA · GenAI · LLM
type: glossaire
---

# Glossaire IA · GenAI · LLM

Définitions de travail pour la veille. Chaque entrée : `## Terme`, puis des lignes
`Domaine :`, `Alias :`, `Mots-clés :`, `Source :` (URL primaire), puis la définition.

## Transformer
Domaine : Fondamentaux
Alias : architecture Transformer
Mots-clés : attention, self-attention, encodeur, décodeur
Source : https://arxiv.org/abs/1706.03762

Architecture de réseau de neurones fondée sur l'**attention** : chaque token pondère
l'ensemble des autres tokens de la séquence. Elle a remplacé les réseaux récurrents en
traitement du langage et sert de base à la quasi-totalité des LLM actuels.

## Attention
Domaine : Fondamentaux
Alias : self-attention, mécanisme d'attention
Mots-clés : query, key, value, multi-head
Source : https://arxiv.org/abs/1706.03762

Opération qui calcule, pour chaque position, une moyenne pondérée des représentations
des autres positions (produit scalaire *query* × *key*, puis pondération des *values*).
Son coût croît avec le carré de la longueur de séquence, d'où les nombreuses
optimisations (FlashAttention, GQA, fenêtres glissantes…).

## Token
Domaine : Fondamentaux
Alias : jeton, tokenisation, tokenizer
Mots-clés : BPE, vocabulaire, découpage

Unité élémentaire manipulée par un modèle de langage : un mot, un morceau de mot ou un
caractère selon le *tokenizer*. Les limites de contexte, les prix d'API et les débits
d'inférence s'expriment en tokens.

## Embedding
Domaine : Fondamentaux
Alias : plongement, vecteur sémantique
Mots-clés : représentation vectorielle, similarité cosinus, base vectorielle

Représentation d'un texte (ou d'une image) sous forme de vecteur dense, telle que des
contenus proches en sens soient proches dans l'espace. Base de la recherche sémantique
et du RAG.

## LLM
Domaine : LLM
Alias : grand modèle de langage, large language model
Mots-clés : modèle de fondation, génération de texte, décodeur
Source : https://arxiv.org/abs/2001.08361

Modèle de langage de grande taille (souvent de quelques milliards à plusieurs centaines
de milliards de paramètres) entraîné à prédire le token suivant sur de très grands
corpus, puis généralement ajusté pour suivre des instructions.

## Modèle de fondation
Domaine : LLM
Alias : foundation model, modèle de base, base model
Mots-clés : pré-entraînement, généraliste

Modèle pré-entraîné à grande échelle sur des données générales, destiné à être adapté
(fine-tuning, prompting, RAG) à de nombreuses tâches. Un *base model* n'a pas encore été
aligné pour suivre des instructions.

## Open weights
Domaine : LLM
Alias : poids ouverts, modèle ouvert, open source
Mots-clés : licence, Hugging Face, téléchargement

Modèle dont les poids sont téléchargeables. À distinguer de l'open source au sens strict :
données et code d'entraînement ne sont pas toujours publiés, et la licence peut restreindre
l'usage commercial. Toujours lire la licence et la *model card*.

## Model card
Domaine : LLM
Alias : fiche modèle
Mots-clés : documentation, licence, limites, évaluation
Source : https://huggingface.co/docs/hub/model-cards

Documentation d'un modèle : usage prévu, données, évaluations, limites, biais et licence.
C'est la source primaire à privilégier pour vérifier une annonce de modèle.

## Fenêtre de contexte
Domaine : LLM
Alias : context window, contexte long, long context
Mots-clés : tokens, longueur de contexte

Nombre maximal de tokens (prompt + réponse) qu'un modèle traite en une fois. Une grande
fenêtre ne garantit pas une bonne utilisation de l'information placée au milieu du
contexte.

## Lost in the middle
Domaine : LLM
Alias : perte au milieu du contexte
Mots-clés : contexte long, position, rappel
Source : https://arxiv.org/abs/2307.03172

Phénomène mesuré sur des LLM : l'information placée au milieu d'un long contexte est
moins bien exploitée que celle placée au début ou à la fin.

## RoPE
Domaine : LLM
Alias : rotary position embedding, encodage positionnel rotatif
Mots-clés : position, extension de contexte
Source : https://arxiv.org/abs/2104.09864

Encodage des positions par rotation des vecteurs *query* et *key*, largement utilisé dans
les LLM récents ; plusieurs techniques d'extension de contexte agissent sur ses paramètres.

## Mixture of Experts
Domaine : LLM
Alias : MoE, mélange d'experts
Mots-clés : routage, paramètres actifs, sparse
Source : https://arxiv.org/abs/1701.06538

Architecture où un routeur n'active qu'une partie des sous-réseaux (« experts ») pour
chaque token. Le nombre total de paramètres est grand mais le nombre de paramètres
**actifs** par token, qui détermine le coût de calcul, est bien plus faible.

## GQA
Domaine : Inférence
Alias : grouped-query attention, MQA, multi-query attention
Mots-clés : KV cache, mémoire
Source : https://arxiv.org/abs/2305.13245

Variante de l'attention où plusieurs têtes de *query* partagent les mêmes têtes de *key*
et *value*, ce qui réduit la taille du KV cache et accélère l'inférence.

## State Space Model
Domaine : LLM
Alias : SSM, Mamba
Mots-clés : séquence longue, linéaire, alternative au Transformer
Source : https://arxiv.org/abs/2312.00752

Famille d'architectures séquentielles dont le coût croît linéairement avec la longueur ;
Mamba en est l'exemple le plus connu. Souvent combinées à de l'attention dans des
modèles hybrides.

## Lois d'échelle
Domaine : Entraînement
Alias : scaling laws, Chinchilla
Mots-clés : calcul, paramètres, données, compute-optimal
Source : https://arxiv.org/abs/2203.15556

Relations empiriques entre la performance d'un modèle et la taille du modèle, la quantité
de données et le calcul d'entraînement. Les travaux « Chinchilla » ont montré qu'à budget
fixe, beaucoup de modèles étaient sous-entraînés en données.

## Pré-entraînement
Domaine : Entraînement
Alias : pretraining
Mots-clés : corpus, next-token prediction, auto-supervisé

Première phase d'entraînement, auto-supervisée, sur un très grand corpus : le modèle
apprend à prédire le token suivant. C'est la phase la plus coûteuse en calcul.

## Fine-tuning
Domaine : Entraînement
Alias : ajustement fin, SFT, supervised fine-tuning
Mots-clés : adaptation, instruction tuning

Entraînement complémentaire d'un modèle pré-entraîné sur des données ciblées (tâche,
domaine, format d'instructions). Le SFT utilise des paires instruction → réponse.

## LoRA
Domaine : Entraînement
Alias : low-rank adaptation, PEFT
Mots-clés : adaptateur, fine-tuning léger
Source : https://arxiv.org/abs/2106.09685

Méthode de fine-tuning efficace : les poids d'origine sont gelés et l'on entraîne de
petites matrices de faible rang ajoutées à certaines couches. Réduit fortement la mémoire
et le stockage nécessaires.

## QLoRA
Domaine : Entraînement
Alias : quantized LoRA
Mots-clés : 4 bits, fine-tuning sur GPU grand public
Source : https://arxiv.org/abs/2305.14314

LoRA appliqué à un modèle de base quantifié en 4 bits, pour fine-tuner de grands modèles
avec peu de mémoire GPU.

## RLHF
Domaine : Entraînement
Alias : reinforcement learning from human feedback, apprentissage par renforcement à partir de retours humains
Mots-clés : alignement, modèle de récompense, préférences
Source : https://arxiv.org/abs/2203.02155

Alignement d'un modèle sur des préférences humaines : on entraîne un modèle de récompense
sur des comparaisons de réponses, puis on optimise le LLM contre cette récompense.

## DPO
Domaine : Entraînement
Alias : direct preference optimization
Mots-clés : préférences, alignement sans modèle de récompense
Source : https://arxiv.org/abs/2305.18290

Méthode d'alignement qui optimise directement le modèle sur des paires de préférences,
sans entraîner de modèle de récompense séparé ni boucle de renforcement.

## Constitutional AI
Domaine : Sécurité
Alias : IA constitutionnelle, RLAIF
Mots-clés : principes, retours IA, innocuité
Source : https://arxiv.org/abs/2212.08073

Méthode d'alignement où le modèle critique et révise ses réponses selon une liste de
principes écrits (« constitution »), avec des retours générés par IA plutôt qu'humains.

## Distillation
Domaine : Entraînement
Alias : knowledge distillation, distillation de connaissances
Mots-clés : modèle enseignant, modèle élève, petit modèle
Source : https://arxiv.org/abs/1503.02531

Entraînement d'un petit modèle (élève) à reproduire les sorties d'un grand modèle
(enseignant). Nombre de « petits » modèles récents sont distillés.

## Données synthétiques
Domaine : Entraînement
Alias : synthetic data
Mots-clés : génération de données, augmentation

Données d'entraînement ou d'évaluation générées par un modèle. Utiles pour couvrir des
cas rares ; risque de contamination et d'amplification d'erreurs si non filtrées.

## Quantification
Domaine : Inférence
Alias : quantization, INT4, INT8, FP8
Mots-clés : précision, compression, mémoire, bits
Source : https://arxiv.org/abs/2210.17323

Réduction de la précision numérique des poids (et parfois des activations) — FP16 vers
INT8, FP8 ou 4 bits — pour réduire la mémoire et accélérer l'inférence, au prix d'une
perte de qualité à mesurer.

## GPTQ
Domaine : Inférence
Alias : post-training quantization
Mots-clés : quantification 4 bits, PTQ
Source : https://arxiv.org/abs/2210.17323

Méthode de quantification après entraînement qui compense couche par couche l'erreur
introduite, permettant des modèles en 3–4 bits avec une faible perte.

## AWQ
Domaine : Inférence
Alias : activation-aware weight quantization
Mots-clés : quantification, poids saillants
Source : https://arxiv.org/abs/2306.00978

Méthode de quantification des poids qui protège les canaux les plus importants,
identifiés à partir des activations.

## GGUF
Domaine : Inférence
Alias : format GGUF
Mots-clés : fichier modèle, quantification, CPU, local, llama.cpp
Source : https://huggingface.co/docs/hub/gguf

Format de fichier de modèles utilisé par llama.cpp et ses dérivés (dont Ollama), qui
embarque poids quantifiés et métadonnées pour l'inférence locale.

## Safetensors
Domaine : Inférence
Alias : format safetensors
Mots-clés : sérialisation, sécurité, poids
Source : https://huggingface.co/docs/safetensors

Format de stockage de tenseurs sûr (pas d'exécution de code au chargement, contrairement
à pickle) et rapide à charger ; standard de fait sur Hugging Face.

## KV cache
Domaine : Inférence
Alias : cache clé-valeur
Mots-clés : mémoire GPU, décodage, contexte long

Mémorisation des *keys* et *values* déjà calculées pendant la génération, pour ne pas les
recalculer à chaque token. Sa taille (proportionnelle au contexte et au nombre de
requêtes) est souvent le goulet d'étranglement mémoire du serving.

## PagedAttention
Domaine : Inférence
Alias : attention paginée
Mots-clés : serving, KV cache, débit, vLLM
Source : https://arxiv.org/abs/2309.06180

Gestion du KV cache par pages, inspirée de la mémoire virtuelle, introduite avec vLLM :
réduit la fragmentation et augmente le nombre de requêtes servies en parallèle.

## vLLM
Domaine : Inférence
Alias : serveur vLLM
Mots-clés : serving, débit, continuous batching
Source : https://github.com/vllm-project/vllm

Moteur open source de serving de LLM à haut débit (PagedAttention, batching continu,
API compatible OpenAI).

## llama.cpp
Domaine : Inférence
Alias : ggml
Mots-clés : inférence locale, CPU, edge, GGUF
Source : https://github.com/ggml-org/llama.cpp

Moteur d'inférence C/C++ pour exécuter des LLM en local (CPU, GPU grand public, Apple
Silicon, edge), au format GGUF.

## FlashAttention
Domaine : Inférence
Alias : flash attention
Mots-clés : noyau GPU, mémoire, IO-aware
Source : https://arxiv.org/abs/2205.14135

Implémentation exacte de l'attention qui minimise les accès à la mémoire GPU ; accélère
l'entraînement et l'inférence, surtout sur contextes longs.

## Décodage spéculatif
Domaine : Inférence
Alias : speculative decoding, draft model
Mots-clés : latence, modèle brouillon, vérification
Source : https://arxiv.org/abs/2211.17192

Un petit modèle propose plusieurs tokens, que le grand modèle vérifie en une passe ; la
sortie est identique en distribution mais la latence baisse.

## Batching continu
Domaine : Inférence
Alias : continuous batching, in-flight batching
Mots-clés : débit, serving, requêtes concurrentes

Ordonnancement qui insère de nouvelles requêtes dans le lot en cours de génération au lieu
d'attendre la fin du lot ; augmente fortement le débit d'un serveur.

## Latence et débit
Domaine : Inférence
Alias : TTFT, tokens par seconde, throughput
Mots-clés : performance, benchmark d'inférence

Métriques de serving : *time to first token* (TTFT), tokens/s par requête et débit
agrégé. Un chiffre de performance n'a de sens qu'avec matériel, taille de lot, longueurs
d'entrée/sortie et précision précisés.

## Prompt engineering
Domaine : Prompting
Alias : ingénierie de prompt, conception de prompt
Mots-clés : instructions, few-shot, system prompt

Conception des instructions, exemples et formats donnés au modèle pour obtenir une
sortie fiable. Comprend le *system prompt*, le *few-shot* et les contraintes de format.

## Few-shot
Domaine : Prompting
Alias : zero-shot, in-context learning, apprentissage en contexte
Mots-clés : exemples, prompting

Fournir quelques exemples résolus dans le prompt pour guider le modèle, sans modifier
ses poids (*zero-shot* : aucun exemple).

## Chain-of-thought
Domaine : Prompting
Alias : CoT, chaîne de raisonnement
Mots-clés : raisonnement, étapes intermédiaires
Source : https://arxiv.org/abs/2201.11903

Faire produire au modèle des étapes de raisonnement intermédiaires avant la réponse ;
améliore les tâches de raisonnement multi-étapes.

## Self-consistency
Domaine : Prompting
Alias : auto-cohérence, vote majoritaire
Mots-clés : échantillonnage, raisonnement
Source : https://arxiv.org/abs/2203.11171

Échantillonner plusieurs raisonnements et retenir la réponse majoritaire.

## Tree of Thoughts
Domaine : Prompting
Alias : ToT, arbre de pensées
Mots-clés : recherche, raisonnement, planification
Source : https://arxiv.org/abs/2305.10601

Exploration de plusieurs pistes de raisonnement sous forme d'arbre, avec évaluation et
retour arrière.

## Modèle de raisonnement
Domaine : LLM
Alias : reasoning model, thinking, test-time compute
Mots-clés : raisonnement, calcul à l'inférence, réflexion

Modèle entraîné à produire un raisonnement étendu avant de répondre, en échangeant du
calcul à l'inférence contre de la précision. Les gains annoncés doivent être comparés à
budget de calcul équivalent.

## Sortie structurée
Domaine : Prompting
Alias : structured output, JSON mode, décodage contraint
Mots-clés : schéma JSON, Pydantic, grammaire

Contraindre la génération à respecter un schéma (JSON Schema, grammaire). Ce projet
valide toute sortie LLM par Pydantic avant persistance.

## Hallucination
Domaine : Évaluation
Alias : confabulation, affirmation non fondée
Mots-clés : factualité, ancrage, vérification

Contenu généré plausible mais faux ou non étayé par les sources. Se réduit par l'ancrage
(RAG, citations obligatoires) et la vérification, sans disparaître.

## RAG
Domaine : RAG
Alias : retrieval-augmented generation, génération augmentée par la recherche
Mots-clés : recherche, base de connaissances, ancrage, citations
Source : https://arxiv.org/abs/2005.11401

Architecture qui récupère des passages pertinents (moteur de recherche, base vectorielle)
et les injecte dans le prompt pour que le modèle réponde en s'appuyant dessus et puisse
citer ses sources.

## Chunking
Domaine : RAG
Alias : découpage, segmentation de documents
Mots-clés : passages, taille de chunk, chevauchement

Découpage des documents en passages avant indexation. La taille et le chevauchement des
*chunks* influencent fortement la qualité du RAG.

## Base vectorielle
Domaine : RAG
Alias : vector database, vector store, ANN
Mots-clés : embeddings, recherche approximative, similarité

Stockage d'embeddings avec recherche des plus proches voisins (souvent approximative,
ANN). Outil courant des pipelines RAG.

## Recherche hybride
Domaine : RAG
Alias : hybrid search, BM25
Mots-clés : lexical, sémantique, fusion

Combinaison d'une recherche lexicale (BM25, plein texte) et d'une recherche sémantique
(embeddings). Ce projet utilise SQLite FTS5 (BM25) pour la recherche lexicale.

## Reranking
Domaine : RAG
Alias : reranker, re-classement, cross-encoder, ColBERT
Mots-clés : précision, top-k
Source : https://arxiv.org/abs/2004.12832

Second passage qui re-classe les candidats de la recherche avec un modèle plus précis
(cross-encoder, interaction tardive type ColBERT).

## GraphRAG
Domaine : RAG
Alias : RAG sur graphe, knowledge graph RAG
Mots-clés : graphe de connaissances, entités, relations

Variante du RAG qui construit ou exploite un graphe d'entités et de relations pour
répondre aux questions de synthèse ou multi-sauts.

## Agent
Domaine : Agents
Alias : agent IA, agent LLM, agentic
Mots-clés : outils, boucle, planification, autonomie

Système où un LLM décide d'actions (appels d'outils, recherche, code) dans une boucle,
observe les résultats et poursuit jusqu'à l'objectif. Autonomie, outils et garde-fous
en sont les trois dimensions clés.

## Tool calling
Domaine : Agents
Alias : function calling, appel d'outils, tool use
Mots-clés : outils, API, schéma d'arguments
Source : https://arxiv.org/abs/2302.04761

Capacité d'un modèle à produire un appel structuré à un outil externe (nom + arguments),
exécuté par l'application qui renvoie le résultat au modèle.

## ReAct
Domaine : Agents
Alias : reason + act
Mots-clés : raisonnement, action, observation
Source : https://arxiv.org/abs/2210.03629

Schéma d'agent qui alterne raisonnement, action (outil) et observation.

## MCP
Domaine : Agents
Alias : Model Context Protocol
Mots-clés : outils, serveurs, protocole, intégrations
Source : https://modelcontextprotocol.io

Protocole ouvert qui standardise l'exposition d'outils, de ressources et de prompts à des
applications LLM via des serveurs. Ce projet expose GitHub et Notion via des serveurs MCP.

## A2A
Domaine : Agents
Alias : Agent2Agent, agent-to-agent
Mots-clés : interopérabilité, protocole, multi-agents
Source : https://a2a-protocol.org

Protocole ouvert de communication entre agents de fournisseurs différents (découverte
des capacités, échange de tâches).

## Multi-agents
Domaine : Agents
Alias : système multi-agents, supervisor, orchestration
Mots-clés : sous-agents, graphe, coordination

Organisation de plusieurs agents spécialisés coordonnés par un superviseur ou un graphe.
Ce projet utilise un supervisor LangGraph et des sous-agents Scout, Critic et Editor.

## LangGraph
Domaine : Agents
Alias : graphe d'agents
Mots-clés : state graph, checkpoint, interrupt, human-in-the-loop
Source : https://docs.langchain.com/oss/python/langgraph/overview

Bibliothèque de construction d'agents sous forme de graphes d'états, avec persistance
(checkpoints), reprise et interruptions pour validation humaine.

## Human-in-the-loop
Domaine : Agents
Alias : HITL, validation humaine
Mots-clés : approbation, interrupt, supervision

Point de contrôle où un humain valide, corrige ou rejette une action ou une sortie avant
qu'elle ne soit exécutée ou publiée.

## Mémoire d'agent
Domaine : Agents
Alias : mémoire long terme, memory
Mots-clés : store, préférences, leçons

Informations conservées entre sessions (préférences, leçons, faits) et réinjectées dans
les prompts. Ce projet utilise le Store LangGraph.

## Benchmark
Domaine : Évaluation
Alias : jeu d'évaluation, banc d'essai
Mots-clés : score, méthodologie, leaderboard, contamination

Jeu de tâches standardisé pour comparer des modèles. Un score n'est interprétable qu'avec
la version du benchmark, le protocole (few-shot, CoT, nombre d'essais) et le contrôle de
contamination.

## MMLU
Domaine : Évaluation
Alias : Massive Multitask Language Understanding
Mots-clés : QCM, connaissances, benchmark
Source : https://arxiv.org/abs/2009.03300

Benchmark de QCM couvrant 57 disciplines ; largement saturé par les modèles récents.

## HumanEval
Domaine : Évaluation
Alias : pass@k
Mots-clés : code, génération de code
Source : https://arxiv.org/abs/2107.03374

Benchmark de génération de fonctions Python évaluées par tests unitaires (métrique pass@k).

## SWE-bench
Domaine : Évaluation
Alias : SWE-bench Verified
Mots-clés : agents de code, issues GitHub, ingénierie logicielle
Source : https://arxiv.org/abs/2310.06770

Benchmark où un modèle doit résoudre de vraies issues GitHub, vérifiées par les tests
du dépôt. Préciser la variante (complète, Lite, Verified) et le harnais d'agent utilisé.

## LLM-as-a-judge
Domaine : Évaluation
Alias : LLM juge, évaluation par LLM
Mots-clés : évaluation automatique, biais de position
Source : https://arxiv.org/abs/2306.05685

Utiliser un LLM pour noter ou comparer des réponses. Pratique mais sujet à des biais
(position, longueur, auto-préférence) à contrôler.

## Arena
Domaine : Évaluation
Alias : LMArena, Chatbot Arena, Elo
Mots-clés : préférences humaines, classement
Source : https://lmarena.ai

Classement de modèles fondé sur des votes humains en comparaison à l'aveugle.

## lm-evaluation-harness
Domaine : Évaluation
Alias : harnais d'évaluation
Mots-clés : reproductibilité, open source
Source : https://github.com/EleutherAI/lm-evaluation-harness

Outil open source d'évaluation reproductible de modèles de langage sur de nombreux
benchmarks.

## Contamination
Domaine : Évaluation
Alias : fuite de données de test, data contamination
Mots-clés : benchmark, surapprentissage

Présence des données de test d'un benchmark dans les données d'entraînement : gonfle
les scores sans refléter une vraie capacité.

## Observabilité LLM
Domaine : MLOps
Alias : tracing, traces, LLMOps
Mots-clés : Langfuse, LangSmith, OpenTelemetry
Source : https://opentelemetry.io/docs/specs/semconv/gen-ai/

Collecte des traces d'appels (prompts, sorties, latence, coût, tokens) pour déboguer et
évaluer une application LLM. OpenTelemetry définit des conventions pour l'IA générative.

## Injection de prompt
Domaine : Sécurité
Alias : prompt injection, injection indirecte
Mots-clés : contenu non fiable, exfiltration, jailbreak
Source : https://genai.owasp.org/llm-top-10/

Instructions malveillantes insérées dans l'entrée ou dans un contenu récupéré (page web,
document) pour détourner le modèle. Ce projet neutralise les motifs connus dans les
contenus collectés (`sanitize_untrusted`).

## Jailbreak
Domaine : Sécurité
Alias : contournement des garde-fous
Mots-clés : red teaming, sécurité

Prompt conçu pour faire produire au modèle un contenu que ses règles interdisent.

## Red teaming
Domaine : Sécurité
Alias : tests adversariaux
Mots-clés : évaluation de sécurité, attaques

Évaluation adversariale d'un modèle ou d'un système pour découvrir failles et
comportements dangereux avant déploiement.

## Guardrails
Domaine : Sécurité
Alias : garde-fous
Mots-clés : filtrage, validation, politique

Contrôles déterministes ou modèles autour d'un LLM : validation d'entrée/sortie, listes
blanches d'outils, filtrage de contenu. Voir `app/harness/guards.py` et `hooks.py`.

## Diffusion
Domaine : Multimodal
Alias : modèle de diffusion, latent diffusion
Mots-clés : génération d'images, débruitage, vidéo
Source : https://arxiv.org/abs/2112.10752

Modèles génératifs qui apprennent à débruiter progressivement un signal ; la diffusion
latente opère dans un espace compressé. Base de la plupart des générateurs d'images
et de vidéo.

## Modèle multimodal
Domaine : Multimodal
Alias : VLM, vision-language model, multimodal
Mots-clés : image, audio, vidéo, vision, CLIP
Source : https://arxiv.org/abs/2103.00020

Modèle qui traite ou génère plusieurs modalités (texte, image, audio, vidéo). CLIP a
popularisé l'alignement texte-image par apprentissage contrastif.

## VLA
Domaine : Robotique
Alias : vision-language-action
Mots-clés : robotique, politique, manipulation

Modèle qui, à partir d'images et d'instructions en langage naturel, produit des actions
pour un robot.

## Edge AI
Domaine : Inférence
Alias : IA embarquée, on-device, inférence locale
Mots-clés : Jetson, mobile, faible consommation

Exécution de modèles sur l'appareil (poste, mobile, carte embarquée) plutôt que dans le
cloud : confidentialité, latence, coût, au prix de modèles plus petits ou quantifiés.

## AI Act
Domaine : Réglementation
Alias : règlement européen sur l'IA, RIA, Règlement (UE) 2024/1689
Mots-clés : GPAI, haut risque, conformité, UE
Source : https://eur-lex.europa.eu/eli/reg/2024/1689/oj

Règlement (UE) 2024/1689 établissant des règles harmonisées sur l'intelligence
artificielle : approche par niveaux de risque et obligations spécifiques pour les
modèles d'IA à usage général (GPAI).
