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

    assert storage.schema_version(connection) == 3
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
