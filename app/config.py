from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Ollama natif (http://host:11434). Un suffixe /v1 hérité de l'API
    # OpenAI-compatible est toléré et retiré automatiquement.
    # ollama (défaut) | openai (tout serveur compatible OpenAI) | anthropic (API Claude native)
    llm_provider: Literal["ollama", "openai", "anthropic"] = "ollama"
    anthropic_api_key: str | None = None  # sinon ANTHROPIC_API_KEY / profil `ant auth login`
    llm_base_url: str = "http://127.0.0.1:11434"
    llm_api_key: str = "ollama"
    llm_model: str = "gemma-3-4b-it"
    llm_temperature: float = 0.1
    llm_num_ctx: int = 8192
    llm_timeout: int = 180

    # Budgets du harness : bornent le coût et la durée d'une exécution.
    max_llm_calls: int = 20
    max_documents_per_run: int = 24
    scout_batch_size: int = 8
    max_signals: int = 8
    max_age_days: int = 14
    min_relevance: int = 6
    # Sous ce nombre de signaux acceptés, le supervisor relance la recherche.
    min_accepted: int = 3
    max_review_rounds: int = 1
    max_repair_rounds: int = 1
    # Qualité (sous-graphe quality) : titres similaires au-delà de ce seuil = doublons (Jaccard 0-1),
    # pool du prefilter = max_documents × facteur (bruit et doublons retirés avant le Scout).
    dedup_threshold: float = Field(0.7, ge=0.3, le=1.0)
    quality_pool_factor: int = Field(3, ge=1, le=6)
    # Poids du ranking hybride, JSON (ex. {"llm": 0.6, "freshness": 0.1}) ; clés : llm, freshness,
    # profile, source, corroboration. Les clés absentes gardent leur valeur par défaut.
    rank_weights: dict[str, float] = Field(default_factory=dict)
    # Sélection diversifiée : pénalité (points /100) par signal déjà retenu de la même source,
    # moitié par thème partagé ; 0 = simple top-k du ranking.
    diversity_penalty: float = Field(8.0, ge=0, le=50)
    # Claims et preuves (sous-graphe evidence) : signaux par appel LLM, claims par signal,
    # caractères d'extrait transmis (et référence de l'ancrage des citations)
    evidence_enabled: bool = True
    evidence_batch_size: int = Field(3, ge=1, le=8)
    evidence_max_claims: int = Field(3, ge=1, le=6)
    evidence_excerpt_chars: int = Field(1200, ge=300, le=6000)
    # Profil d'impact versionné (TOML suivi par Git) ; fichier absent = pas de profil
    impact_profile_path: Path = PROJECT_ROOT / "profiles/julien.toml"
    # Mémoire typée : durée de vie d'une leçon humaine ; règles suggérées après N rejets d'un thème
    memory_lesson_ttl_days: int = Field(180, ge=1, le=3650)
    rule_suggestion_min_rejections: int = Field(3, ge=2, le=50)
    rule_suggestion_ttl_days: int = Field(30, ge=1, le=365)

    github_token: str | None = None
    github_api_url: str | None = None  # défaut du serveur MCP : api.github.com
    arxiv_max_results: int = 20
    rss_max_items: int = 8  # entrées lues par flux RSS

    sources_path: Path = PROJECT_ROOT / "sources.toml"
    skill_path: Path = PROJECT_ROOT / ".claude/skills/veille-tech/SKILL.md"

    database_path: Path = Path("data/watch.db")
    checkpoint_db_path: Path = Path("data/checkpoints.db")
    memory_db_path: Path = Path("data/memory.db")  # Store LangGraph
    claude_memory_path: Path = PROJECT_ROOT / ".claude/memory/veille.md"
    output_dir: Path = Path("output")

    human_approval: bool = True

    langfuse_enabled: bool = False
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_project_id: str | None = None  # facultatif : évite un appel API pour construire les liens

    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "llm-watch-harness"
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    # Notion (serveur MCP notion-veille) : jeton d'intégration interne + page parente
    notion_token: str | None = None
    notion_parent_page_id: str | None = None
    notion_api_url: str | None = None  # défaut du serveur MCP : api.notion.com
    notion_digests: int = 10

    reports_dir: Path = Path("reports")
    templates_dir: Path = PROJECT_ROOT / "templates"
    prompt_overrides_dir: Path = Path("data/prompts")  # prompts édités depuis l'interface

    # Base de connaissances : fichiers du dépôt + fichiers Markdown téléversés
    knowledge_dir: Path = PROJECT_ROOT / "knowledge"
    knowledge_uploads_dir: Path = Path("data/knowledge")

    # Review d'une actualité par URL
    review_skill_path: Path = PROJECT_ROOT / ".claude/skills/review-actu/SKILL.md"
    review_max_chars: int = 12000  # texte d'article transmis au Reviewer
    review_max_bytes: int = 3_000_000  # taille maximale d'une page téléchargée
    review_timeout: int = 20
    review_allow_private: bool = False  # URLs vers le réseau local (déconseillé)
    # Actus en cartes : pages de blog crawlées + recherche web par l'API Claude (outil web_search)
    # clé API Claude (CLAUDE_API ou CLAUDE_API_KEY), sinon ANTHROPIC_API_KEY
    claude_api: str | None = Field(None, validation_alias=AliasChoices("CLAUDE_API", "CLAUDE_API_KEY"))
    # Clé non rattachée à un workspace : l'API exige l'en-tête anthropic-workspace-id
    claude_workspace_id: str | None = Field(None, validation_alias=AliasChoices("CLAUDE_WORKSPACE_ID", "ANTHROPIC_WORKSPACE_ID"))
    news_model: str = "claude-sonnet-5"
    assistant_model: str = "claude-sonnet-5"  # assistant rapide (panneau flottant) : API Claude directe
    news_search_tool: str = "web_search_20250305"
    news_search_max_uses: int = 5  # recherches web par appel (borne le coût)
    news_search_days: int = 7
    news_per_source: int = 24
    # Aperçus des actus sans image : captures par le serveur MCP Playwright (Node.js + Chromium requis)
    screenshot_enabled: bool = False
    screenshot_mcp_command: str = "npx"
    screenshot_mcp_args: list[str] = Field(default_factory=lambda: [
        "-y", "@playwright/mcp@0.0.82", "--headless", "--isolated", "--browser", "chromium", "--block-service-workers",
        "--output-dir", "data/playwright-mcp"])
    screenshot_max_per_run: int = Field(6, ge=1, le=40)  # pages capturées par lot (une à la fois)
    screenshot_timeout: int = Field(45, ge=5, le=300)
    screenshot_settle_seconds: float = Field(3.0, ge=0, le=20)  # attente du rendu JavaScript avant capture
    screenshot_dir: Path = Path("data/screenshots")
    # Événements (onglet 📅) : horizon de recherche, pages d'événements téléchargées en parallèle
    events_horizon_days: int = Field(120, ge=7, le=365)
    events_fetch_workers: int = Field(3, ge=1, le=8)
    # Benchmarks (onglet 📊) : catalogue et classements de BenchLM.ai, détail remis en cache au-delà du délai
    benchmarks_url: str = "https://benchlm.ai"
    benchmarks_detail_ttl_hours: int = Field(24, ge=1, le=24 * 30)
    # Vidéos et podcasts (onglet 🎬) : fenêtre de recherche, entrées lues par flux [[media]]
    media_search_days: int = Field(14, ge=1, le=90)
    media_per_source: int = Field(12, ge=1, le=50)
    # Cron quotidien (python -m app.main cron start) : benchmarks, actus, événements via bin/veille
    cron_hour: int = Field(7, ge=0, le=23)
    cron_minute: int = Field(0, ge=0, le=59)
    cron_timezone: str = "Europe/Paris"
    # JSON, ex. ["benchmarks", "news-crawl"] ; tâches : benchmarks, news-crawl, news-search, events-crawl,
    # events-search, news-screenshots
    cron_tasks: list[str] = Field(default_factory=lambda: [
        "benchmarks", "news-crawl", "news-search", "events-crawl", "events-search", "news-screenshots"])
    cron_task_timeout: int = Field(900, ge=30, le=7200)  # secondes par commande
    cron_retries: int = Field(2, ge=0, le=5)  # nouveaux essais après un échec
    cron_retry_delay: int = Field(60, ge=1, le=3600)  # secondes, doublé à chaque essai
    cron_min_interval_hours: float = Field(20, ge=0, le=24 * 7)  # tâche réussie plus récemment : sautée
    cron_offline_retry_minutes: int = Field(15, ge=1, le=720)
    cron_offline_max_retries: int = Field(8, ge=0, le=100)
    cron_state_path: Path = Path("data/cron-state.json")
    cron_log_path: Path = Path("data/cron.log")
    cron_executable: Path = PROJECT_ROOT / "bin" / "veille"
    web_host: str = "127.0.0.1"
    web_port: int = 8000
    # Jeton exigé sur l'API web (Authorization: Bearer, ou cookie posé par /?token=…) ;
    # indispensable dès que l'interface est exposée hors de la machine (Tailscale Funnel, tunnel)
    web_api_token: str | None = None
    # Verrouillage par login (page /login) : identifiant + mot de passe robuste (≥ WEB_PASSWORD_MIN_LENGTH
    # caractères, minuscule, majuscule, chiffre, caractère spécial ; sinon le serveur refuse de démarrer).
    # La TUI et les scripts passent en HTTP Basic avec les mêmes identifiants.
    web_username: str | None = None
    web_password: str | None = None
    web_password_min_length: int = Field(8, ge=8, le=128)
    web_session_hours: int = Field(12, ge=1, le=24 * 30)
    # Clé de signature des sessions ; vide : clé aléatoire (reconnexion après chaque redémarrage)
    web_session_secret: str | None = None
    web_login_max_failures: int = Field(5, ge=1, le=50)  # puis blocage de l'adresse
    web_login_lock_seconds: int = Field(300, ge=10, le=86400)

    @property
    def web_login_enabled(self) -> bool:
        return bool(self.web_username or self.web_password)

    @property
    def notion_enabled(self) -> bool:
        return bool(self.notion_token and self.notion_parent_page_id)

    @property
    def claude_search_key(self) -> str | None:
        return self.claude_api or self.anthropic_api_key

    @property
    def ollama_url(self) -> str:
        return self.llm_base_url.rstrip("/").removesuffix("/v1")


settings = Settings()
