"""Curated knowledge base with a human-in-the-loop approval workflow.

Read path:  each user message is matched against *approved* Q&A pairs; good
            matches are handed to the model as authoritative answers.
Write path: questions the twin cannot answer are stored as *pending*. The
            owner reviews them (see admin.py), writes an answer and approves
            it, and from then on the twin answers that question correctly.

SQLite keeps this dependency-free. A deployment can use a hosted Turso database
instead, which runs the same SQL and survives restarts of the host.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import TwinConfig
from .turso import TursoConnection

log = logging.getLogger(__name__)

# Tables: qa_pairs (approved answers), qa_fts (full-text search index, kept in sync by the
# triggers), questions (unanswered questions), contacts and interactions (exchange log).
SCHEMA = """
CREATE TABLE IF NOT EXISTS qa_pairs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    question      TEXT NOT NULL,
    answer        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'approved' CHECK (status IN ('approved', 'archived')),
    source        TEXT NOT NULL DEFAULT 'manual',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    times_used    INTEGER NOT NULL DEFAULT 0,
    last_used_at  TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS qa_fts USING fts5(
    question, answer, content='qa_pairs', content_rowid='id', tokenize='porter'
);

CREATE TRIGGER IF NOT EXISTS qa_ai AFTER INSERT ON qa_pairs BEGIN
    INSERT INTO qa_fts(rowid, question, answer) VALUES (new.id, new.question, new.answer);
END;
CREATE TRIGGER IF NOT EXISTS qa_ad AFTER DELETE ON qa_pairs BEGIN
    INSERT INTO qa_fts(qa_fts, rowid, question, answer) VALUES ('delete', old.id, old.question, old.answer);
END;
CREATE TRIGGER IF NOT EXISTS qa_au AFTER UPDATE OF question, answer ON qa_pairs BEGIN
    INSERT INTO qa_fts(qa_fts, rowid, question, answer) VALUES ('delete', old.id, old.question, old.answer);
    INSERT INTO qa_fts(rowid, question, answer) VALUES (new.id, new.question, new.answer);
END;

CREATE TABLE IF NOT EXISTS questions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    question       TEXT NOT NULL,
    normalised     TEXT NOT NULL UNIQUE,
    status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'answered', 'dismissed')),
    ask_count      INTEGER NOT NULL DEFAULT 1,
    first_asked_at TEXT NOT NULL,
    last_asked_at  TEXT NOT NULL,
    qa_pair_id     INTEGER REFERENCES qa_pairs(id)
);

CREATE TABLE IF NOT EXISTS contacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL UNIQUE,
    name        TEXT,
    notes       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS interactions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT NOT NULL,
    user_message   TEXT NOT NULL,
    response       TEXT,
    kb_match_ids   TEXT,
    tools_used     TEXT,
    tool_rounds    INTEGER,
    hit_round_cap  INTEGER NOT NULL DEFAULT 0,
    latency_ms     INTEGER
);
"""

# Common words ignored when comparing questions, because they say little about the topic.
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "do", "does", "did",
    "you", "your", "yours", "i", "me", "my", "we", "our", "to", "of", "in", "on", "at",
    "for", "with", "and", "or", "what", "which", "who", "how", "when", "where", "why",
    "can", "could", "would", "will", "should", "have", "has", "had", "it", "its", "this",
    "that", "there", "about", "tell", "please", "any", "some", "so", "if", "as", "by",
}


# Current UTC time as an ISO string, used for all timestamps.
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Lowercase and remove punctuation and extra spaces, so similar questions compare equal.
def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


# Crudely strip word endings so that relocate and relocating match.
def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > 4 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    return word.rstrip("e") if len(word) > 4 else word


# The meaningful words in a text, stemmed and without stopwords.
def content_tokens(text: str) -> set[str]:
    return {_stem(w) for w in normalise(text).split() if w not in STOPWORDS}


def similarity(a: str, b: str) -> float:
    """Blend of content-word F1 and character-level ratio, in [0, 1]."""
    # Word overlap catches rewording; the character ratio catches small spelling differences.
    ta, tb = content_tokens(a), content_tokens(b)
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb)
    f1 = 0.0 if overlap == 0 else 2 * overlap / (len(ta) + len(tb))
    ratio = difflib.SequenceMatcher(None, normalise(a), normalise(b)).ratio()
    return round(0.6 * f1 + 0.4 * ratio, 3)


# An approved answer found by search, with its similarity score.
@dataclass
class QAMatch:
    id: int
    question: str
    answer: str
    score: float


# A database path starting with one of these is a hosted Turso database, not a local file.
REMOTE_PREFIXES = ("libsql://", "https://", "turso://")


# All database access for approved answers, questions, contacts and logs.
class KnowledgeBase:
    def __init__(self, db_path: str | Path, auth_token: str | None = None):
        self.db_path = str(db_path)
        if self.db_path.startswith(REMOTE_PREFIXES):
            self.conn = TursoConnection(self.db_path, auth_token)
        else:
            if self.db_path != ":memory:":
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            # Gradio serves requests from worker threads.
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        # Databases created before the UK spelling change still use the old column name.
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(questions)")}
        if "normalized" in columns:
            self.conn.execute("ALTER TABLE questions RENAME COLUMN normalized TO normalised")
        self.conn.commit()

    # read path
    def search(self, query: str, threshold: float = 0.55, limit: int = 3) -> list[QAMatch]:
        # Full-text search finds candidates cheaply, then similarity() decides which are good enough.
        # Whole words, because FTS5 stems them its own way and wouldn't recognise our shorter stems.
        tokens = sorted({w for w in normalise(query).split() if w not in STOPWORDS})
        if not tokens:
            return []
        # Quoting each word stops special characters being read as search syntax.
        fts_query = " OR ".join(f'"{t}"*' for t in tokens)
        rows = self.conn.execute(
            """
            SELECT q.id, q.question, q.answer
            FROM qa_fts f JOIN qa_pairs q ON q.id = f.rowid
            WHERE qa_fts MATCH ? AND q.status = 'approved'
            ORDER BY bm25(qa_fts) LIMIT 20
            """,
            (fts_query,),
        ).fetchall()
        matches = [
            QAMatch(r["id"], r["question"], r["answer"], similarity(query, r["question"]))
            for r in rows
        ]
        matches = [m for m in matches if m.score >= threshold]
        matches.sort(key=lambda m: m.score, reverse=True)
        return matches[:limit]

    def mark_used(self, qa_ids: list[int]) -> None:
        if not qa_ids:
            return
        self.conn.executemany(
            "UPDATE qa_pairs SET times_used = times_used + 1, last_used_at = ? WHERE id = ?",
            [(_now(), i) for i in qa_ids],
        )
        self.conn.commit()

    # write path
    def record_unknown_question(self, question: str) -> int:
        """Store (or re-count) an unanswered question. Returns its id."""
        # Reworded duplicates share a normalised form, so they raise ask_count instead of adding rows.
        norm = normalise(question)
        now = _now()
        self.conn.execute(
            """
            INSERT INTO questions (question, normalised, first_asked_at, last_asked_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(normalised) DO UPDATE SET
                ask_count = ask_count + 1,
                last_asked_at = excluded.last_asked_at,
                status = CASE WHEN status = 'dismissed' THEN 'pending' ELSE status END
            """,
            (question.strip(), norm, now, now),
        )
        self.conn.commit()
        return self.conn.execute("SELECT id FROM questions WHERE normalised = ?", (norm,)).fetchone()["id"]

    def record_contact(self, email: str, name: str | None = None, notes: str | None = None) -> int:
        now = _now()
        self.conn.execute(
            """
            INSERT INTO contacts (email, name, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                name = COALESCE(excluded.name, contacts.name),
                notes = CASE WHEN excluded.notes IS NULL THEN contacts.notes
                             WHEN contacts.notes IS NULL THEN excluded.notes
                             ELSE contacts.notes || char(10) || excluded.notes END,
                updated_at = excluded.updated_at
            """,
            # Lowercased so the same person isn't saved twice. Notes from repeat visits are appended.
            (email.strip().lower(), name, notes, now, now),
        )
        self.conn.commit()
        return self.conn.execute("SELECT id FROM contacts WHERE email = ?", (email.strip().lower(),)).fetchone()["id"]

    def log_interaction(self, *, user_message, response, kb_match_ids, tools_used,
                        tool_rounds, hit_round_cap, latency_ms) -> None:
        self.conn.execute(
            """
            INSERT INTO interactions (created_at, user_message, response, kb_match_ids,
                                      tools_used, tool_rounds, hit_round_cap, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_now(), user_message, response, json.dumps(kb_match_ids), json.dumps(tools_used),
             tool_rounds, int(hit_round_cap), latency_ms),
        )
        self.conn.commit()

    # admin workflow
    def add_qa(self, question: str, answer: str, source: str = "manual") -> int:
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO qa_pairs (question, answer, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (question.strip(), answer.strip(), source, now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def approve_question(self, question_id: int, answer: str, question_text: str | None = None) -> int:
        """Answer a pending question: creates an approved Q&A pair and links it."""
        row = self.conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
        if row is None:
            raise KeyError(f"No question with id {question_id}")
        qa_id = self.add_qa(question_text or row["question"], answer, source=f"question:{question_id}")
        self.conn.execute(
            "UPDATE questions SET status = 'answered', qa_pair_id = ? WHERE id = ?", (qa_id, question_id)
        )
        self.conn.commit()
        return qa_id

    def dismiss_question(self, question_id: int) -> None:
        self.conn.execute("UPDATE questions SET status = 'dismissed' WHERE id = ?", (question_id,))
        self.conn.commit()

    def update_qa(self, qa_id: int, answer: str) -> None:
        self.conn.execute("UPDATE qa_pairs SET answer = ?, updated_at = ? WHERE id = ?", (answer.strip(), _now(), qa_id))
        self.conn.commit()

    def archive_qa(self, qa_id: int) -> None:
        self.conn.execute("UPDATE qa_pairs SET status = 'archived', updated_at = ? WHERE id = ?", (_now(), qa_id))
        self.conn.commit()

    def pending_questions(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM questions WHERE status = 'pending' ORDER BY ask_count DESC, last_asked_at DESC"
        ).fetchall()

    # Most-used answers first.
    def list_qa(self, include_archived: bool = False) -> list[sqlite3.Row]:
        where = "" if include_archived else "WHERE status = 'approved'"
        return self.conn.execute(f"SELECT * FROM qa_pairs {where} ORDER BY times_used DESC, id").fetchall()

    def list_contacts(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM contacts ORDER BY created_at DESC").fetchall()

    # Summary numbers for `python admin.py stats`.
    def stats(self) -> dict:
        one = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "approved_answers": one("SELECT COUNT(*) FROM qa_pairs WHERE status = 'approved'"),
            "pending_questions": one("SELECT COUNT(*) FROM questions WHERE status = 'pending'"),
            "contacts": one("SELECT COUNT(*) FROM contacts"),
            "interactions": one("SELECT COUNT(*) FROM interactions"),
            "kb_hit_rate": one(
                "SELECT ROUND(AVG(CASE WHEN kb_match_ids != '[]' THEN 1.0 ELSE 0 END), 3) FROM interactions"
            ),
            "round_cap_hits": one("SELECT COALESCE(SUM(hit_round_cap), 0) FROM interactions"),
        }

    # version-controlled seed
    # Write approved answers to the seed file, which is committed so they survive redeploys.
    def export_yaml(self, path: str | Path) -> int:
        import yaml

        rows = self.list_qa()
        data = [{"question": r["question"], "answer": r["answer"]} for r in rows]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
        return len(data)

    def import_yaml(self, path: str | Path) -> int:
        """Add seed answers whose question isn't already approved. Returns count added."""
        import yaml

        entries = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
        # Skip questions already approved, so importing at every startup is safe.
        existing = {normalise(r["question"]) for r in self.list_qa()}
        added = 0
        for e in entries:
            if normalise(e["question"]) not in existing:
                self.add_qa(e["question"], e["answer"], source="seed")
                existing.add(normalise(e["question"]))
                added += 1
        return added

    def close(self) -> None:
        self.conn.close()


# Open the hosted database if its URL is in the environment, otherwise the local file.
def open_knowledge_base(cfg: TwinConfig) -> KnowledgeBase:
    kb_cfg = cfg.knowledge_base
    url = os.getenv(kb_cfg.db_url_env) if kb_cfg.db_url_env else None
    if url:
        log.info("Using the hosted Turso database")
        return KnowledgeBase(url, os.getenv(kb_cfg.db_token_env) if kb_cfg.db_token_env else None)
    log.info("Using the local database at %s", kb_cfg.db_path)
    return KnowledgeBase(cfg.resolve(kb_cfg.db_path))
