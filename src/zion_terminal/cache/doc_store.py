"""SQLite-backed persistent document store for processed filings.

Complements the existing diskcache (TTL-based raw response cache) with
durable storage for cleaned/processed documents.  Team-compatible: stores
CleanedDocument objects for later retrieval by doc_id, ticker, or form.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


class DocumentStore:
    """Persistent store for processed filing documents.

    Usage::

        store = DocumentStore()
        store.store(cleaned_doc)
        doc = store.get("AAPL_10-K_2023-11-03")
        store.close()
    """

    def __init__(self, db_path: str = ".cache/zion/docs.db") -> None:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                form TEXT NOT NULL,
                filing_date TEXT,
                source TEXT DEFAULT 'sec_edgar',
                markdown TEXT,
                markdown_char_count INTEGER DEFAULT 0,
                sections TEXT,
                verification TEXT,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_docs_ticker ON documents(ticker)"
        )
        self._conn.commit()

    def store(self, doc: Any) -> str:
        """Store a CleanedDocument.  Returns doc_id."""
        self._conn.execute(
            """INSERT OR REPLACE INTO documents
               (doc_id, ticker, form, filing_date, source, markdown,
                markdown_char_count, sections, verification, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doc.doc_id, doc.ticker, doc.form, doc.filing_date, doc.source,
                doc.markdown, doc.markdown_char_count,
                json.dumps(doc.sections, default=str),
                json.dumps(doc.verification, default=str),
                json.dumps(doc.metadata, default=str),
                doc.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return doc.doc_id

    def get(self, doc_id: str) -> dict[str, Any] | None:
        """Retrieve a document by ID.  Returns dict or None."""
        row = self._conn.execute(
            "SELECT * FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        for key in ("sections", "verification", "metadata"):
            if d.get(key):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    def list_docs(
        self, ticker: str = "", form: str = "",
    ) -> list[dict[str, Any]]:
        """List stored documents with optional filters."""
        query = "SELECT doc_id, ticker, form, filing_date, created_at FROM documents WHERE 1=1"
        params: list[Any] = []
        if ticker:
            query += " AND ticker = ?"
            params.append(ticker.upper())
        if form:
            query += " AND form = ?"
            params.append(form)
        query += " ORDER BY created_at DESC"
        return [dict(r) for r in self._conn.execute(query, params).fetchall()]

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
