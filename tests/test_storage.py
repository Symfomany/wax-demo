import pytest
import sqlite3

from app import storage
from app.schemas import Document


def test_fresh_database_is_fully_migrated(connection):
    assert storage.schema_version(connection) == len(storage.MIGRATIONS)
    assert set(storage.memory_stats(connection)) >= {"published_items", "feedback", "llm_cache"}


def test_migration_upgrades_legacy_database_and_keeps_data(tmp_path):
    path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(storage.MIGRATIONS[0])  # schéma d'origine, user_version = 0
    legacy.execute(
        "INSERT INTO documents (url, source, title, summary, content, tags_json) "
        "VALUES ('https://example.org/a', 'rss', 'A', '', '', '[]')"
    )
    legacy.commit()
    legacy.close()

    connection = storage.connect(path)

    assert storage.schema_version(connection) == len(storage.MIGRATIONS)
    assert storage.memory_stats(connection)["documents"] == 1
    columns = [row[1] for row in connection.execute("PRAGMA table_info(digests)")]
    assert "run_id" in columns


def test_v3_backfills_full_text_index_for_existing_documents(tmp_path):
    path = tmp_path / "v2.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(storage.MIGRATIONS[0] + storage.MIGRATIONS[1] + "PRAGMA user_version = 2;")
    legacy.execute(
        "INSERT INTO documents (url, source, title, summary, content, tags_json) "
        "VALUES ('https://example.org/q', 'rss', 'Quantization FP8', 'vLLM serving', '', '[]')"
    )
    legacy.commit()
    legacy.close()

    connection = storage.connect(path)

    assert storage.schema_version(connection) == len(storage.MIGRATIONS)
    assert [r["url"] for r in storage.search_documents(connection, "quantization")] == [
        "https://example.org/q"
    ]


def test_full_text_search_ranks_and_tracks_new_documents(connection):
    storage.save_documents(connection, [
        Document(source="rss", title="Agents MCP en production", url="https://example.org/1",
                 summary="Retour d'expérience sur les agents."),
        Document(source="arxiv", title="Vowel shifts", url="https://example.org/2",
                 summary="Phonology of dialects, no agents here? agents."),
    ])

    results = storage.search_documents(connection, "agents MCP")

    assert results[0]["url"] == "https://example.org/1"  # titre pondéré ×3
    assert storage.search_documents(connection, "élève d'été ?!") == []
    assert storage.fts_query('a "; DROP TABLE x --') == '"drop"* OR "table"*'


def test_conversations_and_notion_pages(connection):
    storage.upsert_conversation(connection, "c1", "Quoi de neuf ?")
    storage.upsert_conversation(connection, "c1", "titre ignoré")
    assert [c["title"] for c in storage.list_conversations(connection)] == ["Quoi de neuf ?"]

    assert storage.record_notion_page(connection, "p1", "https://notion.so/p1", [1, 2]) is None
    assert storage.record_notion_page(connection, "p2", "https://notion.so/p2", [3]) == "p1"
    assert storage.current_notion_page(connection)["page_id"] == "p2"


def test_recent_digests_are_most_recent_first(connection, tmp_path):
    for day in ("2026-09-20", "2026-09-24"):
        storage.record_digest(
            connection, day, {"generated_at": f"{day}T08:00:00Z", "items": []},
            tmp_path / "d.md", tmp_path / "d.json",
        )
    assert [d["run_id"] for d in storage.recent_digests(connection, limit=1)] == ["2026-09-24"]


def test_migrate_is_idempotent(connection):
    assert storage.migrate(connection) == storage.migrate(connection) == len(storage.MIGRATIONS)


def test_save_documents_ignores_duplicates(connection):
    document = Document(source="rss", title="A", url="https://example.org/a")
    assert storage.save_documents(connection, [document, document]) == 1


def test_record_digest_feeds_published_memory(connection, tmp_path):
    digest = {
        "generated_at": "2026-09-24T12:00:00Z",
        "items": [{"url": "https://example.org/a", "title": "A"}],
    }
    storage.record_digest(connection, "run-1", digest, tmp_path / "d.md", tmp_path / "d.json")
    assert storage.published_urls(connection) == {"https://example.org/a"}


def test_feedback_and_cache_roundtrip(connection):
    storage.add_feedback(connection, "run-1", False, "Trop d'articles marketing")
    storage.add_feedback(connection, "run-2", True, "")
    assert storage.recent_feedback(connection) == ["Trop d'articles marketing"]

    storage.cache_set(connection, "k", "model", '{"x": 1}')
    assert storage.cache_get(connection, "k") == '{"x": 1}'
    assert storage.cache_get(connection, "absent") is None


def test_traces_roundtrip_and_update(connection):
    steps = [{"graph": "chat", "node": "route", "ms": 3}]
    storage.save_trace(connection, "t1", "chat", "conv-1", "Question ?", steps, {"agents": ["route"]})
    storage.save_trace(connection, "t1", "chat", "conv-1", "Question ?", steps * 2, {"agents": ["route"]})

    trace = storage.get_trace(connection, "t1")
    assert trace["kind"] == "chat" and len(trace["steps"]) == 2
    assert trace["engaged"] == {"agents": ["route"]}
    assert storage.list_traces(connection, "conv-1")[0]["id"] == "t1"
    assert storage.get_trace(connection, "absent") is None


def test_search_filters_sources_dates_publication_and_match_all(connection):
    repo = Document(source="github", title="fast-infer : nouveau dépôt", url="https://example.org/r",
                    summary="Agent inference server.", tags=["github-mcp", "Rust"],
                    published_at="2026-09-20T00:00:00Z")
    release = Document(source="github", title="vllm v1 release", url="https://example.org/v",
                       summary="Agent inference speedups.", tags=["github", "vllm"],
                       published_at="2026-09-01T00:00:00Z")
    paper = Document(source="arxiv", title="Agents benchmark", url="https://example.org/a",
                     summary="Evaluation of agents.", published_at="2026-09-22T00:00:00Z")
    storage.save_documents(connection, [repo, release, paper])
    storage.record_digest(connection, "r", {"generated_at": "2026-09-23T00:00:00Z",
                                            "items": [{"url": "https://example.org/a", "title": "A"}]},
                          "d.md", "d.json")
    urls = lambda **kw: {r["url"] for r in storage.search_documents(connection, **kw)}

    assert urls(text="agent", sources=["github-mcp"]) == {"https://example.org/r"}
    assert urls(text="agent", sources=["github"]) == {"https://example.org/v"}
    assert urls(text="agent", since="2026-09-10") == {"https://example.org/r", "https://example.org/a"}
    assert urls(text="agent inference", match_all=True) == {"https://example.org/r", "https://example.org/v"}
    assert urls(text="", published=True) == {"https://example.org/a"}
    assert urls(text="", sources=["arxiv", "github-mcp"], limit=1) == {"https://example.org/a"}
    assert storage.search_documents(connection, "") == []
    with pytest.raises(ValueError, match="inconnue"):
        storage.search_documents(connection, "x", sources=["twitter"])


def test_v5_adds_reviews_to_a_v4_database(tmp_path):
    path = tmp_path / "v4.db"
    legacy = sqlite3.connect(path)
    legacy.executescript("".join(storage.MIGRATIONS[:4]) + "PRAGMA user_version = 4;")
    legacy.execute("INSERT INTO traces (id, kind, session_id, title, steps_json) VALUES ('t', 'chat', 's', 'T', '[]')")
    legacy.commit()
    legacy.close()

    connection = storage.connect(path)

    assert storage.schema_version(connection) == 5
    assert storage.memory_stats(connection)["traces"] == 1
    assert storage.memory_stats(connection)["reviews"] == 0
    columns = {row[1] for row in connection.execute("PRAGMA table_info(reviews)")}
    assert columns >= {"id", "url", "conversation_id", "revision", "record_json"}


def test_review_round_trip_and_revision(connection):
    record = {"id": "r1", "url": "https://example.org/a", "title": "A", "conversation_id": "c1", "revision": 1,
              "page": {"site": "example.org", "domains": ["LLM"]}, "analysis": {"relevance": 7}}
    storage.save_review(connection, record)
    storage.save_review(connection, record | {"revision": 2, "analysis": {"relevance": 4}})

    saved = storage.get_review(connection, "r1")
    assert (saved["revision"], saved["analysis"]["relevance"]) == (2, 4)
    assert storage.list_reviews(connection) == [{
        "id": "r1", "url": "https://example.org/a", "title": "A", "revision": 2,
        "created_at": saved["created_at"], "updated_at": saved["updated_at"],
        "site": "example.org", "domains": ["LLM"], "relevance": 4,
    }]
    assert storage.get_review(connection, "absent") is None
    with pytest.raises(sqlite3.IntegrityError):  # une conversation de challenge par review
        storage.save_review(connection, record | {"id": "r2"})
