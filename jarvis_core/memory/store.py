"""Gedächtnis in SQLite (FTS5): ausdrücklich gemerkte Fakten und Notizen."""
from __future__ import annotations

import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Optional

_WORD = re.compile(r"[\wäöüÄÖÜß]{3,}", re.UNICODE)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class MemoryStore:
    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS memories(
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL DEFAULT 'fact',
                text TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL)""")
            db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                text, content='memories', content_rowid='id',
                tokenize='unicode61 remove_diacritics 2')""")
            db.executescript("""
                CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memories BEGIN
                  INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text); END;
                CREATE TRIGGER IF NOT EXISTS mem_ad AFTER DELETE ON memories BEGIN
                  INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.id, old.text); END;
                CREATE TRIGGER IF NOT EXISTS mem_au AFTER UPDATE ON memories BEGIN
                  INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.id, old.text);
                  INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text); END;""")

    def _db(self):
        # Eine Verbindung pro Aufruf: thread-sicher, Daten sind sofort dauerhaft (Commit)
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        return _Tx(con)

    # ------------------------------------------------------------ API

    def add(self, text: str, kind: str = "fact") -> tuple[int, bool]:
        """(id, neu?). Identischer Text wird nicht doppelt gespeichert."""
        text = text.strip()
        if not text:
            raise ValueError("Leerer Text")
        kind = kind if kind in ("fact", "note") else "fact"
        with self._db() as db:
            row = db.execute("SELECT id FROM memories WHERE lower(text)=lower(?)", (text,)).fetchone()
            if row:
                return row["id"], False
            cur = db.execute("INSERT INTO memories(kind,text,created,updated) VALUES (?,?,?,?)",
                             (kind, text, _now(), _now()))
            return int(cur.lastrowid or 0), True

    def get(self, mid: int) -> Optional[dict]:
        with self._db() as db:
            row = db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
        return dict(row) if row else None

    def update(self, mid: int, text: str) -> bool:
        with self._db() as db:
            return db.execute("UPDATE memories SET text=?, updated=? WHERE id=?",
                              (text.strip(), _now(), mid)).rowcount > 0

    def delete(self, mid: int) -> bool:
        with self._db() as db:
            return db.execute("DELETE FROM memories WHERE id=?", (mid,)).rowcount > 0

    def recent(self, limit: int = 10) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM memories ORDER BY id DESC LIMIT ?", (limit,))]

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """FTS5 mit Präfix-Stämmen (Geburtstag ≈ Geburtstage); Fallback LIKE."""
        words = _WORD.findall(query.lower())
        if not words:
            return self.recent(limit)
        terms = " OR ".join(f'"{w[:max(4, len(w) - 2)]}"*' for w in words)
        with self._db() as db:
            try:
                rows = db.execute(
                    "SELECT m.* FROM memories_fts f JOIN memories m ON m.id=f.rowid "
                    "WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT ?",
                    (terms, limit)).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                like = " OR ".join("lower(text) LIKE ?" for _ in words)
                rows = db.execute(f"SELECT * FROM memories WHERE {like} LIMIT ?",
                                  [f"%{w[:max(4, len(w) - 2)]}%" for w in words] + [limit]).fetchall()
        return [dict(r) for r in rows]


class _Tx:
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def __enter__(self):
        return self.con

    def __exit__(self, exc_type, *_):
        try:
            (self.con.rollback if exc_type else self.con.commit)()
        finally:
            self.con.close()
