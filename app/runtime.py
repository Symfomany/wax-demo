"""Assemblage du runtime (contexte, graphe, Store, traçage) partagé par la CLI et le web."""

from contextlib import contextmanager
from functools import partial

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore

from app import observability
from app.collectors import default_collectors, load_sources
from app.config import settings
from app.harness.skills import load_skill
from app.llm import get_llm
from app.notion import fetch_og_images, sync_notion
from app.workflow.graph import build_graph
from app.workflow.state import HarnessContext


def make_context(connection, human_approval: bool, collectors=None) -> HarnessContext:
    return HarnessContext(
        llm=get_llm(connection),
        connection=connection,
        skill=load_skill(settings.skill_path),
        output_dir=settings.output_dir,
        collectors=collectors if collectors is not None else default_collectors(),
        human_approval=human_approval,
        max_documents=settings.max_documents_per_run,
        batch_size=settings.scout_batch_size,
        max_signals=settings.max_signals,
        max_age_days=settings.max_age_days,
        min_relevance=settings.min_relevance,
        min_accepted=settings.min_accepted,
        max_review_rounds=settings.max_review_rounds,
        max_repair_rounds=settings.max_repair_rounds,
        claude_memory_path=settings.claude_memory_path,
        reports_dir=settings.reports_dir,
        templates_dir=settings.templates_dir,
        notion_sync=notion_sync(),
        exclude_keywords=load_sources(settings.sources_path).get("github_mcp", {}).get("exclude_keywords", []),
    )


def notion_sync():
    """Outil de synchronisation Notion configuré, ou None si Notion n'est pas configuré."""
    if not settings.notion_enabled:
        return None
    return partial(
        sync_notion,
        token=settings.notion_token,
        parent_page_id=settings.notion_parent_page_id,
        api_url=settings.notion_api_url,
        limit=settings.notion_digests,
        image_lookup=fetch_og_images,
    )


@contextmanager
def open_store():
    settings.memory_db_path.parent.mkdir(parents=True, exist_ok=True)
    with SqliteStore.from_conn_string(str(settings.memory_db_path)) as store:
        store.setup()
        yield store


@contextmanager
def open_graph(context: HarnessContext):
    settings.checkpoint_db_path.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(settings.checkpoint_db_path)) as saver, open_store() as store:
        yield build_graph(saver, context, store)


def graph_config(run_id: str) -> dict:
    return {
        "configurable": {"thread_id": run_id},
        "recursion_limit": 60,
        **observability.trace_config(run_id, "veille", trace_seed=run_id),
    }
