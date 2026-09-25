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
    web_host: str = "127.0.0.1"
    web_port: int = 8000
    # Jeton exigé sur l'API web (Authorization: Bearer, ou cookie posé par /?token=…) ;
    # indispensable dès que l'interface est exposée hors de la machine (Tailscale Funnel, tunnel)
    web_api_token: str | None = None

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
