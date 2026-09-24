import functools
import json
import re
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path

from app.schemas import Document


# Chaque migration est appliquée une seule fois, dans l'ordre ; la version
# courante est stockée dans PRAGMA user_version. Ne jamais modifier une
# migration existante : en ajouter une nouvelle (et son test).
MIGRATIONS: list[str] = [
    # v1 — schéma initial
    """
    CREATE TABLE IF NOT EXISTS documents (
        url TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        title TEXT NOT NULL,
        published_at TEXT,
        summary TEXT NOT NULL,
        content TEXT NOT NULL,
        tags_json TEXT NOT NULL,
        collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS digests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        generated_at TEXT NOT NULL,
        markdown_path TEXT NOT NULL,
        json_path TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
    """,
    # v2 — mémoire de veille : runs, items publiés, retours humains, cache LLM
    """
    ALTER TABLE digests ADD COLUMN run_id TEXT;

    CREATE TABLE runs (
        run_id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        status TEXT NOT NULL,
        stats_json TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE published_items (
        url TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        digest_id INTEGER REFERENCES digests(id),
        published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        approved INTEGER NOT NULL,
        note TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE llm_cache (
        key TEXT PRIMARY KEY,
        model TEXT NOT NULL,
        response_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    # v3 — chat (index des conversations), pages Notion, recherche plein texte
    """
    CREATE TABLE conversations (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE notion_pages (
        page_id TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        digest_ids_json TEXT NOT NULL,
        archived INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE VIRTUAL TABLE documents_fts USING fts5(
        title, summary, content='documents', content_rowid='rowid',
        tokenize='unicode61 remove_diacritics 2'
    );
    INSERT INTO documents_fts(rowid, title, summary)
        SELECT rowid, title, summary FROM documents;

    CREATE TRIGGER documents_fts_insert AFTER INSERT ON documents BEGIN
        INSERT INTO documents_fts(rowid, title, summary)
        VALUES (new.rowid, new.title, new.summary);
    END;
    CREATE TRIGGER documents_fts_delete AFTER DELETE ON documents BEGIN
        INSERT INTO documents_fts(documents_fts, rowid, title, summary)
        VALUES ('delete', old.rowid, old.title, old.summary);
    END;
    CREATE TRIGGER documents_fts_update AFTER UPDATE ON documents BEGIN
        INSERT INTO documents_fts(documents_fts, rowid, title, summary)
        VALUES ('delete', old.rowid, old.title, old.summary);
        INSERT INTO documents_fts(rowid, title, summary)
        VALUES (new.rowid, new.title, new.summary);
    END;
    """,
    # v4 — traces : parcours dans le graphe de chaque réponse du chat et de chaque veille
    """
    CREATE TABLE traces (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        session_id TEXT NOT NULL,
        title TEXT NOT NULL,
        steps_json TEXT NOT NULL,
        engaged_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX traces_session ON traces(session_id);
    """,
    # v5 — reviews d'actualités par URL (onglet Review) et conversation de challenge associée
    """
    CREATE TABLE reviews (
        id TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        title TEXT NOT NULL,
        conversation_id TEXT NOT NULL UNIQUE,
        revision INTEGER NOT NULL DEFAULT 1,
        record_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX reviews_updated ON reviews(updated_at);
    """,
    # v6 — actus en cartes (onglet Actus) : blogs crawlés, flux RSS et recherche web Claude
    """
    CREATE TABLE news (
        id TEXT PRIMARY KEY,
        url TEXT NOT NULL UNIQUE,
        source TEXT NOT NULL,
        origin TEXT NOT NULL,
        title TEXT NOT NULL,
        published_at TEXT,
        record_json TEXT NOT NULL,
        fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX news_published ON news(published_at);
    """,
]


# Une connexion est partagée entre les threads des nœuds LangGraph parallèles :
# tous les accès passent par ce verrou réentrant.
_LOCK = threading.RLock()


def locked(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return function(*args, **kwargs)

    return wrapper


def schema_version(connection: sqlite3.Connection) -> int:
    return connection.execute("PRAGMA user_version").fetchone()[0]


def migrate(connection: sqlite3.Connection) -> int:
    version = schema_version(connection)

    for index, script in enumerate(MIGRATIONS[version:], start=version + 1):
        # executescript valide toute transaction en cours : on encadre
        # explicitement pour que migration + version soient atomiques.
        connection.executescript(
            f"BEGIN;\n{script}\nPRAGMA user_version = {index};\nCOMMIT;"
        )

    return schema_version(connection)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # LangGraph peut exécuter les nœuds dans un thread de travail.
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    migrate(connection)
    return connection


# --- Documents -------------------------------------------------------------


@locked
def save_documents(connection: sqlite3.Connection, documents: Iterable[Document]) -> int:
    count = 0

    for document in documents:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO documents
            (url, source, title, published_at, summary, content, tags_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(document.url),
                document.source,
                document.title,
                document.published_at.isoformat() if document.published_at else None,
                document.summary,
                document.content,
                json.dumps(document.tags),
            ),
        )
        count += cursor.rowcount

    connection.commit()
    return count


@locked
def recent_documents(connection: sqlite3.Connection, limit: int = 60) -> list[Document]:
    rows = connection.execute(
        """
        SELECT source, title, url, published_at, summary, content, tags_json
        FROM documents
        ORDER BY COALESCE(published_at, collected_at) DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    return [
        Document(
            source=row[0],
            title=row[1],
            url=row[2],
            published_at=row[3],
            summary=row[4],
            content=row[5],
            tags=json.loads(row[6]),
        )
        for row in rows
    ]


# --- Mémoire de veille -----------------------------------------------------


@locked
def published_urls(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute("SELECT url FROM published_items")}


@locked
def record_digest(
    connection: sqlite3.Connection,
    run_id: str,
    digest: dict,
    markdown_path: Path,
    json_path: Path,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO digests (generated_at, markdown_path, json_path, payload_json, run_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            digest["generated_at"],
            str(markdown_path),
            str(json_path),
            json.dumps(digest, ensure_ascii=False),
            run_id,
        ),
    )
    digest_id = cursor.lastrowid
    connection.executemany(
        "INSERT OR IGNORE INTO published_items (url, title, digest_id) VALUES (?, ?, ?)",
        [(item["url"], item["title"], digest_id) for item in digest["items"]],
    )
    connection.commit()
    return digest_id


@locked
def add_feedback(
    connection: sqlite3.Connection, run_id: str, approved: bool, note: str
) -> None:
    connection.execute(
        "INSERT INTO feedback (run_id, approved, note) VALUES (?, ?, ?)",
        (run_id, int(approved), note),
    )
    connection.commit()


@locked
def recent_feedback(connection: sqlite3.Connection, limit: int = 5) -> list[str]:
    rows = connection.execute(
        "SELECT note FROM feedback WHERE note != '' ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [row[0] for row in rows]


@locked
def set_run_status(
    connection: sqlite3.Connection, run_id: str, status: str, stats: dict | None = None
) -> None:
    connection.execute(
        """
        INSERT INTO runs (run_id, status, stats_json) VALUES (?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
            status = excluded.status,
            stats_json = CASE WHEN excluded.stats_json = '{}'
                              THEN runs.stats_json ELSE excluded.stats_json END
        """,
        (run_id, status, json.dumps(stats or {})),
    )
    connection.commit()


@locked
def cache_get(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute(
        "SELECT response_json FROM llm_cache WHERE key = ?", (key,)
    ).fetchone()
    return row[0] if row else None


@locked
def cache_set(connection: sqlite3.Connection, key: str, model: str, response_json: str) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO llm_cache (key, model, response_json) VALUES (?, ?, ?)",
        (key, model, response_json),
    )
    connection.commit()


@locked
def memory_stats(connection: sqlite3.Connection) -> dict[str, int]:
    tables = [
        "documents", "digests", "published_items", "feedback", "llm_cache", "runs",
        "conversations", "notion_pages", "traces", "reviews", "news",
    ]
    return {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in tables
    }


# --- Digests, recherche, chat, Notion (v3) --------------------------------------


@locked
def recent_digests(connection: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = connection.execute(
        """
        SELECT id, run_id, generated_at, markdown_path, json_path, payload_json
        FROM digests ORDER BY generated_at DESC, id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "id": row[0],
            "run_id": row[1],
            "generated_at": row[2],
            "markdown_path": row[3],
            "json_path": row[4],
            "digest": json.loads(row[5]),
        }
        for row in rows
    ]


def fts_query(text: str, match_all: bool = False) -> str:
    """Requête FTS5 sûre : mots alphanumériques en préfixe, reliés par OR (ou AND)."""
    words = [word for word in re.findall(r"\w+", text.lower()) if len(word) > 2]
    return (" AND " if match_all else " OR ").join(f'"{word}"*' for word in words[:12])


# Filtre « source » de l'interface : github-mcp (nouveaux dépôts) est distingué des releases.
SOURCE_FILTERS = {
    "rss": "d.source = 'rss'",
    "arxiv": "d.source = 'arxiv'",
    "github": "(d.source = 'github' AND d.tags_json NOT LIKE '%\"github-mcp\"%')",
    "github-mcp": "d.tags_json LIKE '%\"github-mcp\"%'",
}


@locked
def search_documents(
    connection: sqlite3.Connection,
    text: str,
    limit: int = 6,
    sources: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    published: bool | None = None,
    match_all: bool = False,
) -> list[dict]:
    """Recherche plein texte (BM25, titre ×3) avec filtres ; sans mots-clés, parcours
    chronologique des documents qui passent les filtres."""
    query = fts_query(text, match_all)
    filters, params = [], []
    if sources:
        unknown = set(sources) - set(SOURCE_FILTERS)
        if unknown:
            raise ValueError(f"Source(s) inconnue(s) : {', '.join(sorted(unknown))}")
        filters.append("(" + " OR ".join(SOURCE_FILTERS[source] for source in sources) + ")")
    if since:
        filters.append("COALESCE(d.published_at, d.collected_at) >= ?")
        params.append(since)
    if until:
        filters.append("COALESCE(d.published_at, d.collected_at) < ?")
        params.append(until)
    if published is not None:
        filters.append(("" if published else "NOT ") + "d.url IN (SELECT url FROM published_items)")
    if not query and not filters:
        return []

    columns = """d.url, d.title, d.source, d.published_at, d.summary, d.tags_json,
                 d.url IN (SELECT url FROM published_items) AS published"""
    where = " AND ".join(filters)
    if query:
        sql = f"""SELECT {columns} FROM documents_fts f JOIN documents d ON d.rowid = f.rowid
                  WHERE documents_fts MATCH ? {"AND " + where if where else ""}
                  ORDER BY bm25(documents_fts, 3.0, 1.0), d.published_at DESC LIMIT ?"""
        params = [query, *params]
    else:
        sql = f"""SELECT {columns} FROM documents d WHERE {where}
                  ORDER BY COALESCE(d.published_at, d.collected_at) DESC LIMIT ?"""
    rows = connection.execute(sql, (*params, max(1, min(limit, 50)))).fetchall()
    return [
        {
            "url": row[0],
            "title": row[1],
            "source": "github-mcp" if '"github-mcp"' in row[5] else row[2],
            "published_at": row[3],
            "summary": row[4][:600],
            "published": bool(row[6]),
        }
        for row in rows
    ]


@locked
def upsert_conversation(connection: sqlite3.Connection, conversation_id: str, title: str) -> None:
    connection.execute(
        """
        INSERT INTO conversations (id, title) VALUES (?, ?)
        ON CONFLICT(id) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
        """,
        (conversation_id, title[:80]),
    )
    connection.commit()


@locked
def list_conversations(connection: sqlite3.Connection, limit: int = 30) -> list[dict]:
    rows = connection.execute(
        "SELECT id, title, updated_at FROM conversations ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [{"id": row[0], "title": row[1], "updated_at": row[2]} for row in rows]


@locked
def record_notion_page(
    connection: sqlite3.Connection, page_id: str, url: str, digest_ids: list[int]
) -> str | None:
    """Enregistre la nouvelle page ; renvoie l'id de la page active précédente."""
    previous = connection.execute(
        "SELECT page_id FROM notion_pages WHERE archived = 0 ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    connection.execute("UPDATE notion_pages SET archived = 1 WHERE archived = 0")
    connection.execute(
        "INSERT INTO notion_pages (page_id, url, digest_ids_json) VALUES (?, ?, ?)",
        (page_id, url, json.dumps(digest_ids)),
    )
    connection.commit()
    return previous[0] if previous else None


@locked
def current_notion_page(connection: sqlite3.Connection) -> dict | None:
    row = connection.execute(
        "SELECT page_id, url, created_at FROM notion_pages WHERE archived = 0 "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return {"page_id": row[0], "url": row[1], "created_at": row[2]} if row else None


# --- Traces (v4) -----------------------------------------------------------------


@locked
def save_trace(
    connection: sqlite3.Connection, trace_id: str, kind: str, session_id: str, title: str,
    steps: list[dict], engaged: dict | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO traces (id, kind, session_id, title, steps_json, engaged_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET steps_json = excluded.steps_json,
                                      engaged_json = excluded.engaged_json
        """,
        (trace_id, kind, session_id, title[:120], json.dumps(steps, ensure_ascii=False),
         json.dumps(engaged or {}, ensure_ascii=False)),
    )
    connection.commit()


@locked
def get_trace(connection: sqlite3.Connection, trace_id: str) -> dict | None:
    row = connection.execute(
        "SELECT id, kind, session_id, title, steps_json, engaged_json, created_at FROM traces WHERE id = ?",
        (trace_id,),
    ).fetchone()
    if not row:
        return None
    return {"id": row[0], "kind": row[1], "session_id": row[2], "title": row[3],
            "steps": json.loads(row[4]), "engaged": json.loads(row[5]), "created_at": row[6]}


@locked
def list_traces(connection: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = connection.execute(
        "SELECT id, kind, title, created_at, engaged_json FROM traces WHERE session_id = ? "
        "ORDER BY created_at, rowid",
        (session_id,),
    ).fetchall()
    return [{"id": r[0], "kind": r[1], "title": r[2], "created_at": r[3], "engaged": json.loads(r[4])}
            for r in rows]


# --- Reviews (v5) ----------------------------------------------------------------


@locked
def save_review(connection: sqlite3.Connection, record: dict) -> None:
    """Insère une review ou remplace sa révision (le record est validé par app.review avant)."""
    connection.execute(
        """
        INSERT INTO reviews (id, url, title, conversation_id, revision, record_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET revision = excluded.revision,
                                      record_json = excluded.record_json,
                                      updated_at = CURRENT_TIMESTAMP
        """,
        (record["id"], record["url"], record["title"][:300], record["conversation_id"],
         record["revision"], json.dumps(record, ensure_ascii=False)),
    )
    connection.commit()


@locked
def get_review(connection: sqlite3.Connection, review_id: str) -> dict | None:
    row = connection.execute(
        "SELECT record_json, created_at, updated_at FROM reviews WHERE id = ?", (review_id,)
    ).fetchone()
    return json.loads(row[0]) | {"created_at": row[1], "updated_at": row[2]} if row else None


@locked
def published_reviews(connection: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """Reviews publiées dans Notion (marque `notion` du record), les plus récentes d'abord."""
    rows = connection.execute(
        "SELECT record_json FROM reviews WHERE json_extract(record_json, '$.notion') IS NOT NULL "
        "ORDER BY updated_at DESC, rowid DESC LIMIT ?", (limit,),
    ).fetchall()
    return [json.loads(row[0]) for row in rows]


@locked
def list_reviews(connection: sqlite3.Connection, limit: int = 30) -> list[dict]:
    rows = connection.execute(
        "SELECT id, url, title, revision, created_at, updated_at, record_json FROM reviews "
        "ORDER BY updated_at DESC, rowid DESC LIMIT ?",
        (limit,),
    ).fetchall()
    reviews = []
    for row in rows:
        record = json.loads(row[6])
        reviews.append({"id": row[0], "url": row[1], "title": row[2], "revision": row[3],
                        "created_at": row[4], "updated_at": row[5],
                        "site": record["page"].get("site", ""), "domains": record["page"].get("domains", []),
                        "relevance": record["analysis"]["relevance"], "notion": record.get("notion")})
    return reviews


# --- Actus (v6) ------------------------------------------------------------------


@locked
def save_news(connection: sqlite3.Connection, records: Iterable[dict]) -> int:
    """Insère ou met à jour des actus (records déjà validés par app.news.NewsItem)."""
    count = 0
    for record in records:
        connection.execute(
            """
            INSERT INTO news (id, url, source, origin, title, published_at, record_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET source = excluded.source, origin = excluded.origin,
                                           title = excluded.title,
                                           published_at = COALESCE(excluded.published_at, news.published_at),
                                           record_json = excluded.record_json,
                                           fetched_at = CURRENT_TIMESTAMP
            """,
            (record["id"], record["url"], record["source"], record["origin"], record["title"][:300],
             record.get("published_at"), json.dumps(record, ensure_ascii=False)),
        )
        count += 1
    connection.commit()
    return count


@locked
def list_news(connection: sqlite3.Connection, source: str | None = None, origin: str | None = None,
              query: str | None = None, limit: int = 60, offset: int = 0) -> list[dict]:
    """Actus les plus récentes d'abord (date de publication, sinon date de collecte)."""
    clauses, params = [], []
    if source:
        clauses.append("source = ?")
        params.append(source)
    if origin:
        clauses.append("origin = ?")
        params.append(origin)
    if query:
        clauses.append("(title LIKE ? OR record_json LIKE ?)")
        params += [f"%{query}%", f"%{query}%"]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"SELECT record_json, fetched_at FROM news {where} "
        "ORDER BY COALESCE(published_at, substr(fetched_at, 1, 10)) DESC, fetched_at DESC, rowid DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return [json.loads(row[0]) | {"fetched_at": row[1]} for row in rows]


@locked
def news_sources(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        "SELECT source, origin, COUNT(*), MAX(fetched_at) FROM news GROUP BY source, origin ORDER BY COUNT(*) DESC"
    ).fetchall()
    return [{"source": r[0], "origin": r[1], "count": r[2], "fetched_at": r[3]} for r in rows]


@locked
def news_urls(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute("SELECT url FROM news")}


@locked
def document_detail(connection: sqlite3.Connection, url: str) -> dict | None:
    """Tout ce que la veille sait d'une URL : document collecté, actu, fiche de digest publiée, review."""
    detail: dict = {"url": url}
    row = connection.execute(
        "SELECT source, title, published_at, summary, content, tags_json, collected_at FROM documents WHERE url = ?",
        (url,),
    ).fetchone()
    if row:
        detail |= {"source": row[0], "title": row[1], "published_at": row[2], "summary": row[3],
                   "content": row[4][:4000], "tags": json.loads(row[5]), "collected_at": row[6]}
    news = connection.execute("SELECT record_json, fetched_at FROM news WHERE url = ?", (url,)).fetchone()
    if news:
        record = json.loads(news[0])
        detail.setdefault("title", record["title"])
        detail.setdefault("published_at", record.get("published_at"))
        if not detail.get("summary"):
            detail["summary"] = record.get("summary", "")
        detail["news"] = {key: record.get(key) for key in ("source", "origin", "category", "image", "accent", "why")}
        detail.setdefault("collected_at", news[1])
    published = connection.execute(
        "SELECT p.digest_id, d.generated_at, d.payload_json FROM published_items p "
        "JOIN digests d ON d.id = p.digest_id WHERE p.url = ?", (url,),
    ).fetchone()
    if published:
        item = next((i for i in json.loads(published[2]).get("items", []) if i.get("url") == url), None)
        if item:
            detail.setdefault("title", item["title"])
            detail["digest"] = {"id": published[0], "generated_at": published[1], "summary": item.get("summary", ""),
                                "why_it_matters": item.get("why_it_matters", ""), "claims": item.get("claims", [])}
    review = connection.execute(
        "SELECT id, record_json FROM reviews WHERE url = ? OR json_extract(record_json, '$.page.final_url') = ? "
        "ORDER BY updated_at DESC LIMIT 1", (url, url),
    ).fetchone()
    if review:
        analysis = json.loads(review[1])["analysis"]
        detail["review"] = {"id": review[0], "relevance": analysis["relevance"], "summary": analysis["summary"]}
    return detail if len(detail) > 1 else None
