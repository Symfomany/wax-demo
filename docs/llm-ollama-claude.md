# LLM : Ollama en local, et compatibilité avec l'API Claude

Le projet choisit son modèle via **`LLM_PROVIDER`** (`app/llm.py`). Trois fournisseurs, une seule
interface (`StructuredLLM`) : sortie JSON validée par Pydantic, une relance avec l'erreur, cache
SQLite, budget d'appels.

| `LLM_PROVIDER` | Cible | Sortie structurée | Usage conseillé |
|:--|:--|:--|:--|
| `ollama` (défaut) | API native Ollama `/api/chat` | **contrainte** par schéma (`format=<JSON schema>`) | développement, confidentialité, zéro coût |
| `openai` | tout serveur compatible OpenAI : Ollama `/v1`, vLLM, LM Studio… et la **couche de compatibilité OpenAI de l'API Claude** | `response_format` + schéma rappelé dans le prompt + validation | tests, comparaison de modèles |
| `anthropic` | **API Claude native** (SDK officiel `anthropic`) | **contrainte** (Structured Outputs, `messages.parse`) | qualité maximale du Critic et de l'Editor |

Le chat utilise le modèle conversationnel correspondant (`get_chat_model()` : `ChatOllama`,
`ChatOpenAI` ou `ChatAnthropic`).

---

## 1. Ollama (défaut)

```bash
ollama serve                              # ou : sudo systemctl start ollama
ollama list                               # nom exact du modèle
ollama run gemma-3-4b-it "Réponds OK"     # test ; `ollama ps` doit indiquer 100% GPU
```

```dotenv
LLM_PROVIDER=ollama
LLM_BASE_URL=http://127.0.0.1:11434       # un suffixe /v1 est retiré automatiquement
LLM_MODEL=gemma-3-4b-it
LLM_NUM_CTX=8192
```

Ce que fait le projet avec Ollama :

- **JSON contraint** : `ChatOllama(format=<schéma Pydantic en JSON Schema>)` ; les `$ref` sont
  inlinés (`inline_refs`) car la grammaire d'Ollama les gère mal.
- **Fenêtre de contexte explicite** : `num_ctx` (sinon Ollama tronque sans prévenir).
- **Modèles « completion » suffisants** : pas besoin de *tool calling* natif ; le routeur du chat
  utilise lui aussi une sortie structurée (`ChatDecision`).

Modèles installés sur la machine de référence : `gemma-3-4b-it` (completion) et `qwen:latest`
(qwen3 4B : tools, thinking, completion). Comparer : `LLM_MODEL=qwen:latest python -m app.main run`.

### Ollama via son API compatible OpenAI

```dotenv
LLM_PROVIDER=openai
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_API_KEY=ollama                        # valeur quelconque, exigée par le client OpenAI
LLM_MODEL=gemma-3-4b-it
```

Utile pour valider le chemin « compatible OpenAI » avant de pointer vers un autre serveur. Le
chemin natif reste préférable (JSON contraint, `num_ctx`).

---

## 2. API Claude au format OpenAI (couche de compatibilité)

Anthropic fournit une couche permettant d'utiliser le SDK OpenAI avec l'API Claude. D'après la
documentation officielle (« OpenAI SDK compatibility ») :

- **Base URL** : `https://api.anthropic.com/v1/` ; **clé** : une clé API Claude ; **modèle** : un nom
  de modèle Claude.
- **Finalité** : *« primarily intended to test and compare model capabilities, and is not
  considered a long-term or production-ready solution for most use cases »*.
- **`response_format` est ignoré** et le paramètre `strict` des outils aussi : pas de garantie de
  schéma JSON. Pour cela, la documentation renvoie aux Structured Outputs de l'API native.
- Les messages `system` / `developer` sont **regroupés en tête** de conversation.
- `temperature` accepté entre 0 et 1 ; `n` doit valoir 1 ; `logprobs`, `seed`, `presence_penalty`…
  sont ignorés ; pas de prompt caching ; la réflexion (`thinking`) passe par `extra_body` mais son
  contenu n'est pas renvoyé.

Configuration :

```dotenv
LLM_PROVIDER=openai
LLM_BASE_URL=https://api.anthropic.com/v1/
LLM_API_KEY=<clé API Claude>              # dans .env uniquement, jamais dans Git
LLM_MODEL=claude-opus-5
```

Comment le projet compense les limites :

| Limite | Compensation dans `openai_invoke` |
|:--|:--|
| `response_format` ignoré | le schéma JSON est **aussi rappelé dans le prompt** |
| pas de garantie de schéma | validation Pydantic + **une relance** avec le message d'erreur |
| température refusée par les modèles Claude récents | **non envoyée** quand le modèle commence par `claude` |

À réserver aux tests et aux comparaisons, comme le recommande la documentation.

---

## 3. API Claude native (recommandé pour Claude)

```dotenv
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=<clé API Claude>        # ou profil `ant auth login`, lu par le SDK
LLM_MODEL=claude-opus-5
```

Implémentation (`anthropic_invoke`) :

```python
client.beta.messages.parse(
    model=settings.llm_model,
    max_tokens=16000,
    messages=messages,
    output_format=ScoutOutput,                      # modèle Pydantic → Structured Outputs
    betas=["server-side-fallback-2026-07-01"],
    fallbacks="default",                            # en cas de refus, relance côté serveur
)
```

- **Structured Outputs** : la réponse est contrainte au schéma ; `parsed_output` est déjà validé.
- **Pas de `temperature`** : refusée (erreur 400) par les modèles Claude récents.
- **Réflexion adaptative** active par défaut sur Claude Opus 5 : le texte est lu via `.text`
  (les blocs `thinking` sont ignorés) dans le chat.
- **Refus** : `stop_reason == "refusal"` lève une erreur explicite ; les *fallbacks* côté serveur
  sont activés (`fallbacks: "default"`), ils relancent une requête refusée sur un autre modèle.
- **Coût** : tarif de Claude Opus 5 (5 $ / 25 $ par million de jetons en entrée / sortie, tarif
  public au moment de la rédaction). Mesurer la consommation réelle d'une veille dans Langfuse
  ou LangSmith plutôt que l'estimer.

### Confidentialité

Avec `openai` vers l'API Claude ou `anthropic`, **les extraits des sources collectées et vos
questions partent vers l'API**. Les sources sont publiques, mais vos leçons et conversations
aussi sont envoyées : gardez `ollama` pour tout contenu sensible.

---

## 4. Choisir

| Besoin | Réglage |
|:--|:--|
| Développer, tester, rien ne sort de la machine | `ollama` + `gemma-3-4b-it` |
| Comparer rapidement Claude sans changer de code client | `openai` + `https://api.anthropic.com/v1/` |
| Meilleur Critic / Editor en production | `anthropic` + `claude-opus-5` |
| Serveur d'inférence maison (vLLM…) | `openai` + URL du serveur |

Tous les modes sont testés hors ligne contre de fausses API HTTP (`tests/test_providers.py`), qui
vérifient la requête réellement envoyée (schéma, `fallbacks`, absence de `temperature`).
