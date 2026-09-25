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
    assert storage.published_titles(connection) == [("https://example.org/a", "A")]


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

    assert storage.schema_version(connection) == len(storage.MIGRATIONS)
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
        "site": "example.org", "domains": ["LLM"], "relevance": 4, "notion": None,
    }]
    assert storage.get_review(connection, "absent") is None
    with pytest.raises(sqlite3.IntegrityError):  # une conversation de challenge par review
        storage.save_review(connection, record | {"id": "r2"})


def test_v6_adds_news_to_a_v5_database(tmp_path):
    path = tmp_path / "v5.db"
    legacy = sqlite3.connect(path)
    legacy.executescript("".join(storage.MIGRATIONS[:5]) + "PRAGMA user_version = 5;")
    legacy.execute("INSERT INTO reviews (id, url, title, conversation_id, record_json) VALUES ('r', 'u', 'T', 'c', '{}')")
    legacy.commit()
    legacy.close()

    connection = storage.connect(path)

    assert storage.schema_version(connection) == len(storage.MIGRATIONS)
    assert storage.memory_stats(connection)["reviews"] == 1
    assert storage.memory_stats(connection)["news"] == 0
    columns = {row[1] for row in connection.execute("PRAGMA table_info(news)")}
    assert columns >= {"id", "url", "source", "origin", "title", "published_at", "record_json", "fetched_at"}


def test_news_upsert_order_and_filters(connection):
    def item(n, date, source="Claude Blog", origin="blog"):
        return {"id": f"n{n}", "url": f"https://example.org/{n}", "title": f"Actu {n}", "source": source,
                "origin": origin, "published_at": date, "summary": f"résumé {n}"}

    storage.save_news(connection, [item(1, "2026-09-20"), item(2, "2026-09-24"),
                                   item(3, None, "Web (Claude)", "web_search")])
    storage.save_news(connection, [item(1, None) | {"title": "Actu 1 bis"}])  # date conservée

    news = storage.list_news(connection)
    assert [n["id"] for n in news][1:] == ["n2", "n1"]  # sans date : date de collecte (aujourd'hui) d'abord
    assert storage.list_news(connection, source="Claude Blog")[1]["title"] == "Actu 1 bis"
    assert connection.execute("SELECT published_at FROM news WHERE id = 'n1'").fetchone()[0] == "2026-09-20"
    assert [n["id"] for n in storage.list_news(connection, origin="web_search")] == ["n3"]
    assert [n["id"] for n in storage.list_news(connection, query="résumé 2")] == ["n2"]
    assert {(s["source"], s["count"]) for s in storage.news_sources(connection)} == {("Claude Blog", 2), ("Web (Claude)", 1)}


def test_news_offset_and_known_urls(connection):
    storage.save_news(connection, [{"id": f"n{i}", "url": f"https://e.org/{i}", "title": f"T{i}", "source": "S",
                                    "origin": "rss", "published_at": f"2026-09-{i:02d}"} for i in range(1, 6)])
    assert [n["id"] for n in storage.list_news(connection, limit=2, offset=2)] == ["n3", "n2"]
    assert storage.news_urls(connection) == {f"https://e.org/{i}" for i in range(1, 6)}


def test_document_detail_merges_document_news_digest_and_review(connection, tmp_path):
    from app.schemas import Document

    url = "https://blog.example.org/fp8"
    storage.save_documents(connection, [Document(source="rss", title="FP8", url=url, summary="Résumé collecté",
                                                 content="Texte collecté", tags=["rss", "vllm"])])
    storage.save_news(connection, [{"id": "n", "url": url, "title": "FP8", "source": "Blog", "origin": "blog",
                                    "image": "https://img/x.svg", "accent": "#6a9bcc"}])
    digest = {"generated_at": "2026-09-24T08:00:00Z", "executive_summary": "E", "items": [
        {"title": "FP8", "url": url, "source": "rss", "summary": "Résumé digest", "why_it_matters": "Important",
         "claims": ["2x"], "tags": []}]}
    storage.record_digest(connection, "run", digest, tmp_path / "d.md", tmp_path / "d.json")
    storage.save_review(connection, {"id": "r", "url": url, "title": "FP8", "conversation_id": "c", "revision": 1,
                                     "page": {"final_url": url}, "analysis": {"relevance": 8, "summary": "Revue"}})

    detail = storage.document_detail(connection, url)

    assert (detail["title"], detail["summary"], detail["content"]) == ("FP8", "Résumé collecté", "Texte collecté")
    assert detail["news"]["image"] == "https://img/x.svg"
    assert detail["digest"]["why_it_matters"] == "Important" and detail["digest"]["claims"] == ["2x"]
    assert detail["review"] == {"id": "r", "relevance": 8, "summary": "Revue"}
    assert storage.document_detail(connection, "https://inconnue.org") is None


def test_published_reviews_are_those_marked_notion(connection):
    base = {"url": "https://e.org", "title": "T", "conversation_id": "c", "revision": 1, "page": {}, "analysis": {"relevance": 1}}
    storage.save_review(connection, base | {"id": "a", "conversation_id": "c1"})
    storage.save_review(connection, base | {"id": "b", "conversation_id": "c2", "notion": {"page_url": "u", "published_at": "t"}})
    assert [r["id"] for r in storage.published_reviews(connection)] == ["b"]
    assert {r["id"]: r["notion"] for r in storage.list_reviews(connection)}["a"] is None


def test_v7_v8_add_events_and_media_to_a_v6_database(tmp_path):
    path = tmp_path / "v6.db"
    legacy = sqlite3.connect(path)
    legacy.executescript("".join(storage.MIGRATIONS[:6]) + "PRAGMA user_version = 6;")
    legacy.execute("INSERT INTO news (id, url, source, origin, title, record_json) VALUES ('n', 'u', 's', 'rss', 'T', '{}')")
    legacy.commit()
    legacy.close()

    connection = storage.connect(path)

    assert storage.schema_version(connection) == len(storage.MIGRATIONS)
    stats = storage.memory_stats(connection)
    assert (stats["news"], stats["events"], stats["media"]) == (1, 0, 0)
    assert {row[1] for row in connection.execute("PRAGMA table_info(events)")} >= {"url", "kind", "starts_on", "record_json"}
    assert {row[1] for row in connection.execute("PRAGMA table_info(media)")} >= {"url", "kind", "source", "published_at"}


def event(url, starts_on, ends_on=None, kind="conference"):
    return {"id": url[-4:], "url": url, "title": url, "kind": kind, "starts_on": starts_on, "ends_on": ends_on}


def test_events_upcoming_past_and_verified_date_kept(connection):
    storage.save_events(connection, [
        event("https://e.org/past", "2026-09-01"),
        event("https://e.org/soon", "2026-10-02"),
        event("https://e.org/later", "2026-11-20", kind="meetup"),
        event("https://e.org/running", "2026-09-20", ends_on="2026-09-30"),
        event("https://e.org/tbd", None),
    ])
    upcoming = [e["url"] for e in storage.list_events(connection, today="2026-09-25")]
    assert upcoming == ["https://e.org/running", "https://e.org/soon", "https://e.org/later", "https://e.org/tbd"]
    assert [e["url"] for e in storage.list_events(connection, "past", today="2026-09-25")] == ["https://e.org/past"]
    assert [e["url"] for e in storage.list_events(connection, "all", kind="meetup")] == ["https://e.org/later"]

    # Nouvelle collecte sans date : la date vérifiée précédemment est conservée.
    storage.save_events(connection, [event("https://e.org/soon", None)])
    soon = [e for e in storage.list_events(connection, "all") if e["url"] == "https://e.org/soon"][0]
    assert soon["starts_on"] == "2026-10-02"


def test_media_round_trip_and_filters(connection):
    storage.save_media(connection, [
        {"id": "1", "url": "https://youtube.com/watch?v=a", "title": "Talk", "kind": "video", "source": "Chan",
         "published_at": "2026-09-20"},
        {"id": "2", "url": "https://pod.org/ep1", "title": "Episode", "kind": "podcast", "source": "Pod",
         "published_at": "2026-09-22"},
    ])
    assert [m["title"] for m in storage.list_media(connection)] == ["Episode", "Talk"]
    assert [m["title"] for m in storage.list_media(connection, kind="video")] == ["Talk"]
    assert [m["title"] for m in storage.list_media(connection, query="episo")] == ["Episode"]
    assert {(s["source"], s["count"]) for s in storage.media_sources(connection)} == {("Chan", 1), ("Pod", 1)}


def test_v9_benchmarks_catalog_keeps_cached_detail(connection):
    record = {"key": "draco", "name": "DRACO", "category": "agentic", "year": "2026", "description": "d"}
    storage.save_benchmarks(connection, [record, record | {"key": "hle", "name": "HLE", "category": "knowledge", "year": "2025"}])
    assert storage.benchmark_detail_stale(connection, "draco", 24)
    storage.save_benchmark_detail(connection, "draco", {"key": "draco", "leaderboard": []})
    assert not storage.benchmark_detail_stale(connection, "draco", 24)

    storage.save_benchmarks(connection, [record | {"description": "mise à jour"}])  # nouveau crawl du catalogue

    draco = storage.get_benchmark(connection, "draco")
    assert draco["description"] == "mise à jour" and draco["detail"] == {"key": "draco", "leaderboard": []}
    assert [b["key"] for b in storage.list_benchmarks(connection, category="knowledge")] == ["hle"]
    assert [b["key"] for b in storage.list_benchmarks(connection, query="mise à")] == ["draco"]
    assert storage.memory_stats(connection)["benchmarks"] == 2
    assert storage.get_benchmark(connection, "absent") is None
