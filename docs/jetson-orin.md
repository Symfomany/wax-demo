# Transposer le projet sur une NVIDIA Jetson Orin 8 Go

Guide pour faire tourner la veille sur une **Jetson Orin Nano 8 Go** (ou Orin NX 8 Go) : un
petit serveur de veille silencieux, toujours allumé. Les chiffres de performance ne sont pas
donnés ici : ils dépendent du modèle, de la quantification, du mode d'alimentation et de la
version de JetPack ; la section 7 explique comment les **mesurer**.

## 1. Ce qui change par rapport au PC (RTX 3070 Ti)

| Point | PC de développement | Jetson Orin 8 Go | Conséquence |
|:--|:--|:--|:--|
| Mémoire | 8 Go de VRAM dédiés + RAM système | **8 Go partagés** entre CPU et GPU (mémoire unifiée) | le modèle, le cache KV, le système et Python se partagent 8 Go |
| Architecture | x86_64 | **arm64 (aarch64)** | paquets et images Docker arm64 |
| Système | WSL 2 Ubuntu | **JetPack** (Ubuntu + pilotes, CUDA) | version de Python fournie à vérifier (section 3) |
| Stockage | SSD | microSD ou **NVMe** | NVMe fortement conseillé (SQLite en WAL, modèles) |
| Énergie | libre | **modes d'alimentation** (`nvpmodel`) | choisir le mode le plus performant pour l'inférence |

## 2. Recommandations clés

1. **Modèles de 1 à 4 milliards de paramètres, quantifiés en 4 bits** (Q4). Candidats dans Ollama :
   `gemma3:1b`, `gemma3:4b`, `qwen3:4b`, `llama3.2:3b`. Commencer par un 4B et redescendre si la
   mémoire sature.
2. **Réduire le contexte** : `LLM_NUM_CTX=4096` (le cache KV grossit avec le contexte).
3. **Un seul modèle chargé, une seule requête à la fois** :
   `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`.
4. **Lots plus petits** pour le Scout : `SCOUT_BATCH_SIZE=4`, `MAX_DOCUMENTS_PER_RUN=16`,
   `MAX_SIGNALS=6`.
5. **Mode sans interface graphique** pour libérer de la mémoire :
   `sudo systemctl set-default multi-user.target` (réversible avec `graphical.target`).
6. **NVMe + fichier d'échange** : un swap sur NVMe évite les arrêts brutaux en cas de pic
   (ne remplace pas la mémoire : si le modèle « swappe », il devient très lent).
7. **Mode d'alimentation maximal** et ventilation active pendant les veilles
   (`sudo nvpmodel -q` pour voir le mode courant, `sudo nvpmodel -m <id>` pour le changer ;
   les identifiants dépendent du module et de la version de JetPack).

## 3. Installation

### Ollama

Deux options :

- **Script officiel** d'Ollama (installation native, prend en charge les Jetson sous JetPack) :
  `curl -fsSL https://ollama.com/install.sh | sh`
- **Conteneur** via le projet communautaire `jetson-containers` (github.com/dusty-nv/jetson-containers),
  qui fournit des images Ollama construites pour chaque version de JetPack.

Vérifier que le GPU est utilisé : `ollama run gemma3:4b "Réponds OK"` puis `ollama ps`
(colonne PROCESSOR).

### Python ≥ 3.12

Le projet requiert Python 3.12 (`pyproject.toml`). JetPack 6 est basé sur Ubuntu 22.04, dont le
Python système est plus ancien. Options :

| Option | Commande / idée | Avantage |
|:--|:--|:--|
| **Application en conteneur** (recommandé) | image `python:3.12-slim` arm64 ; Ollama reste natif sur l'hôte | isole les dépendances, Ollama garde l'accès direct au GPU |
| `uv` | `uv python install 3.12` puis `uv venv` | simple, sans toucher au système |
| pyenv / PPA | compilation ou paquets tiers | classique |

L'application elle-même n'a pas besoin du GPU : seul Ollama l'utilise.

### Projet

```bash
git clone <votre dépôt> ~/llm-harness && cd ~/llm-harness
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
cp -n .env.example .env
python -m pytest -q && python -m app.main doctor
```

`.env` pour la Jetson :

```dotenv
LLM_PROVIDER=ollama
LLM_MODEL=gemma3:4b
LLM_NUM_CTX=4096
SCOUT_BATCH_SIZE=4
MAX_DOCUMENTS_PER_RUN=16
MAX_SIGNALS=6
MAX_LLM_CALLS=20
```

Le serveur MCP Notion officiel (`npx`) nécessite Node.js arm64 ; le serveur `notion-veille` du
projet (Python) n'en a pas besoin.

## 4. Service permanent

Les unités `systemd/llm-watch.service` et `.timer` fonctionnent telles quelles (adapter le chemin).
Pour l'interface web :

```ini
# ~/.config/systemd/user/llm-watch-web.service
[Service]
WorkingDirectory=%h/llm-harness
ExecStart=%h/llm-harness/.venv/bin/python -m app.main web --host 127.0.0.1 --port 8000
Restart=on-failure
```

**Accès distant** : l'interface n'a pas d'authentification. Ne pas écouter sur `0.0.0.0` sur un
réseau non maîtrisé ; préférer un tunnel SSH depuis le poste :
`ssh -L 8000:127.0.0.1:8000 jetson` puis `http://127.0.0.1:8000`.

## 5. Architectures alternatives

| Architecture | Quand |
|:--|:--|
| Tout sur la Jetson (collecte, agents, interface) | autonomie, confidentialité |
| Jetson = collecte + interface ; LLM sur le PC 3070 Ti (`LLM_BASE_URL=http://<pc>:11434`) | modèles plus gros que ce que 8 Go permettent |
| Jetson = collecte + interface ; LLM via l'API Claude (`LLM_PROVIDER=anthropic`) | qualité maximale, sans charge GPU locale ; les extraits partent vers l'API |
| Mixte : Scout local sur la Jetson, Critic/Editor sur Claude | voir « Second avis Claude » dans `UPGRADE.md` |

## 6. Surveillance

- `jtop` (paquet `jetson-stats`) : mémoire partagée, GPU, températures, mode d'alimentation.
- `ollama ps` : modèle chargé et part GPU.
- Interface : pastilles d'état ; traces `/trace/<id>` pour la durée de chaque nœud.
- `python -m app.main memory` : santé des sources.

## 7. Mesurer plutôt que supposer

1. Débit du modèle : `ollama run gemma3:4b --verbose "Résume en trois phrases l'intérêt d'un LLM local."`
   (lignes *eval rate* en jetons/s).
2. Mémoire au repos puis pendant une veille : `jtop` ou `free -h`.
3. Durée d'une veille et de chaque agent : onglet Veille puis « Graphe du run » (durées par nœud).
4. Comparer deux modèles : même veille avec `--no-collect` (mêmes documents), `LLM_MODEL` différent,
   puis comparer taux d'acceptation, violations des guards et durées.

## 8. Liste de contrôle

- [ ] JetPack à jour, NVMe monté, swap configuré
- [ ] Mode d'alimentation maximal, ventilateur actif
- [ ] Ollama : `ollama ps` indique le GPU
- [ ] Python 3.12 (uv ou conteneur), `pytest -q` vert
- [ ] `.env` : contexte 4096, lots réduits, modèle ≤ 4B Q4
- [ ] `doctor` vert ; une veille `--no-mcp` complète
- [ ] Service systemd + accès par tunnel SSH
