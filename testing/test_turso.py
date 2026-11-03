# Tests for the hosted database. A fake Turso server runs the requests on a real in-memory SQLite database.
import sqlite3
from types import SimpleNamespace

import pytest

from twin import turso
from twin.knowledge import KnowledgeBase, open_knowledge_base
from twin.turso import TursoError, split_statements

URL = "libsql://twin-example.turso.io"


class FakeTurso:
    """Answers /v2/pipeline requests in the format described in Turso's HTTP reference."""

    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.calls = []
        self.status_code = 200

    @staticmethod
    def _arg(arg):
        return {"null": lambda: None, "integer": lambda: int(arg["value"]),
                "float": lambda: arg["value"], "text": lambda: arg["value"]}[arg["type"]]()

    @staticmethod
    def _cell(value):
        if value is None:
            return {"type": "null"}
        if isinstance(value, int):
            return {"type": "integer", "value": str(value)}
        if isinstance(value, float):
            return {"type": "float", "value": value}
        return {"type": "text", "value": value}

    def _execute(self, stmt):
        try:
            # sqlite3 refuses more than one statement here, like the real API.
            cur = self.db.execute(stmt["sql"], [self._arg(a) for a in stmt.get("args", [])])
            self.db.commit()
        except sqlite3.Error as exc:
            return {"type": "error", "error": {"message": str(exc)}}
        return {"type": "ok", "response": {"type": "execute", "result": {
            "cols": [{"name": d[0], "decltype": None} for d in cur.description or []],
            "rows": [[self._cell(v) for v in row] for row in cur.fetchall()],
            "affected_row_count": max(cur.rowcount, 0),
            "last_insert_rowid": str(cur.lastrowid) if cur.lastrowid else None,
        }}}

    def post(self, url, json, headers, timeout):
        self.calls.append(SimpleNamespace(url=url, headers=headers, timeout=timeout))
        results = [
            self._execute(r["stmt"]) if r["type"] == "execute" else {"type": "ok", "response": {"type": "close"}}
            for r in json["requests"]
        ]
        return SimpleNamespace(ok=self.status_code == 200, status_code=self.status_code,
                               json=lambda: {"baton": None, "base_url": None, "results": results})


@pytest.fixture
def server(monkeypatch):
    fake = FakeTurso()
    monkeypatch.setattr(turso.requests, "post", fake.post)
    return fake


@pytest.fixture
def hosted(server):
    return KnowledgeBase(URL, "test-token")


def test_requests_go_to_the_https_address_with_the_token(server, hosted):
    call = server.calls[-1]
    assert call.url == "https://twin-example.turso.io/v2/pipeline"
    assert call.headers["Authorization"] == "Bearer test-token"
    assert call.timeout


# Triggers contain semicolons, so a naive split would break them.
def test_triggers_are_kept_whole_when_splitting():
    statements = split_statements("CREATE TABLE a (b);\nCREATE TRIGGER t AFTER INSERT ON a BEGIN\n  DELETE FROM a;\nEND;\n")
    assert len(statements) == 2
    assert statements[1].endswith("END;")


# Approved answers must be searchable, which relies on the triggers filling the FTS5 index.
def test_approval_workflow_on_hosted_database(hosted):
    question_id = hosted.record_unknown_question("Would you relocate?")
    assert hosted.record_unknown_question("would you relocate") == question_id
    assert hosted.pending_questions()[0]["ask_count"] == 2

    qa_id = hosted.approve_question(question_id, "Yes, anywhere in South Africa.")
    assert hosted.pending_questions() == []
    match = hosted.search("Would you relocate?")[0]
    assert (match.id, match.answer) == (qa_id, "Yes, anywhere in South Africa.")

    hosted.mark_used([qa_id])
    assert hosted.list_qa()[0]["times_used"] == 1
    hosted.update_qa(qa_id, "Only to Cape Town.")
    assert hosted.search("Would you relocate?")[0].answer == "Only to Cape Town."
    hosted.archive_qa(qa_id)
    assert hosted.search("Would you relocate?") == []


def test_contacts_and_stats_on_hosted_database(hosted):
    empty = hosted.stats()
    assert empty["interactions"] == 0 and empty["kb_hit_rate"] is None

    first = hosted.record_contact("Jane@Example.com", name="Jane")
    assert hosted.record_contact("jane@example.com", notes="Hiring") == first
    contact = hosted.list_contacts()[0]
    assert (contact["name"], contact["notes"]) == ("Jane", "Hiring")

    hosted.log_interaction(user_message="hi", response="hello", kb_match_ids=[], tools_used=["a"],
                           tool_rounds=1, hit_round_cap=True, latency_ms=12)
    stats = hosted.stats()
    assert stats["contacts"] == 1
    assert stats["interactions"] == 1
    assert stats["kb_hit_rate"] == 0.0
    assert stats["round_cap_hits"] == 1


# Starting up runs on every deploy, so it must not fail or duplicate anything the second time.
def test_reopening_keeps_the_data(server, hosted):
    hosted.add_qa("Would you relocate?", "Yes.")
    assert KnowledgeBase(URL, "test-token").list_qa()[0]["answer"] == "Yes."


def test_failed_statement_raises(hosted):
    with pytest.raises(TursoError, match="no such table"):
        hosted.conn.execute("SELECT * FROM missing")


def test_rejected_token_raises(server, hosted):
    server.status_code = 401
    with pytest.raises(TursoError, match="401"):
        hosted.stats()


def test_missing_token_and_wrong_engine_are_refused():
    with pytest.raises(TursoError, match="auth token"):
        KnowledgeBase(URL)
    with pytest.raises(TursoError, match="libSQL"):
        KnowledgeBase("turso://twin-example.turso.io", "test-token")


def test_url_in_environment_selects_the_hosted_database(cfg, server, monkeypatch):
    monkeypatch.setenv("TURSO_DATABASE_URL", URL)
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token")
    assert open_knowledge_base(cfg).db_path == URL


def test_no_url_uses_the_local_file(cfg, tmp_path, monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    cfg.knowledge_base.db_path = str(tmp_path / "local.db")
    kb = open_knowledge_base(cfg)
    assert kb.db_path == cfg.knowledge_base.db_path
    kb.close()


# A database outage loses the lookup and the log entry, but the visitor still gets the model's reply.
def test_chat_still_answers_when_the_database_is_down(make_twin, kb, monkeypatch):
    def down(*args, **kwargs):
        raise TursoError("Turso returned HTTP 503")

    for method in ("search", "mark_used", "log_interaction"):
        monkeypatch.setattr(kb, method, down)
    twin, _, _ = make_twin()
    assert twin.chat("hi", []) == "default answer"
