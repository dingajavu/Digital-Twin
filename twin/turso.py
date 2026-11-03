"""Connection to a hosted Turso database, with the parts of sqlite3's interface the knowledge base uses.

It talks to Turso's HTTP API, so it needs no extra package and there is no open
connection to go stale while the host sleeps. Each statement commits on its own.
"""

from __future__ import annotations

import sqlite3

import requests

# How long to wait for Turso before giving up, in seconds.
TIMEOUT = 10


class TursoError(RuntimeError):
    pass


# Turn a Python value into the typed form the API expects.
def _encode(value) -> dict:
    if value is None:
        return {"type": "null"}
    # Checked before int, because True and False also count as ints in Python.
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "text", "value": str(value)}


# Turn a typed value from the API back into a Python value. Integers arrive as strings.
def _decode(cell: dict):
    if cell["type"] == "null":
        return None
    if cell["type"] == "integer":
        return int(cell["value"])
    if cell["type"] == "float":
        return float(cell["value"])
    return cell["value"]


# Split a script into single statements, because the API runs one at a time.
def split_statements(script: str) -> list[str]:
    statements, current = [], ""
    for line in script.splitlines():
        current += line + "\n"
        # complete_statement knows the semicolons inside a trigger don't end it.
        if sqlite3.complete_statement(current):
            statements.append(current.strip())
            current = ""
    return statements


# One result row, readable by column name or position like sqlite3.Row.
class Row:
    def __init__(self, columns: list[str], values: list):
        self._columns = columns
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._values[self._columns.index(key)]
        return self._values[key]


# The result of one statement, read like a sqlite3 cursor.
class Cursor:
    def __init__(self, result: dict):
        columns = [c["name"] for c in result["cols"]]
        self._rows = [Row(columns, [_decode(cell) for cell in row]) for row in result["rows"]]
        rowid = result.get("last_insert_rowid")
        self.lastrowid = int(rowid) if rowid is not None else None

    def fetchone(self) -> Row | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Row]:
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class TursoConnection:
    def __init__(self, url: str, token: str | None):
        # turso:// databases use a newer engine without the FTS5 search that the knowledge base needs.
        if url.startswith("turso://"):
            raise TursoError("This needs a libSQL database: create it without the --tursodb flag.")
        if not token:
            raise TursoError("A Turso database URL was set without an auth token.")
        self.url = url.replace("libsql://", "https://", 1).rstrip("/")
        self.token = token

    # Send statements in one request and return the result of each, in order.
    def _pipeline(self, statements: list[tuple[str, tuple]]) -> list[dict]:
        requests_ = [
            {"type": "execute", "stmt": {"sql": sql, "args": [_encode(p) for p in params]}}
            for sql, params in statements
        ]
        response = requests.post(
            f"{self.url}/v2/pipeline",
            json={"requests": [*requests_, {"type": "close"}]},
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=TIMEOUT,
        )
        if not response.ok:
            raise TursoError(f"Turso returned HTTP {response.status_code}")
        results = response.json()["results"]
        # A failed statement still returns HTTP 200, with the error in its own result.
        for result in results:
            if result["type"] == "error":
                raise TursoError(result["error"]["message"])
        return [r["response"]["result"] for r in results if r["response"]["type"] == "execute"]

    def execute(self, sql: str, params: tuple = ()) -> Cursor:
        return Cursor(self._pipeline([(sql, params)])[0])

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        self._pipeline([(sql, row) for row in rows])

    def executescript(self, script: str) -> None:
        self._pipeline([(statement, ()) for statement in split_statements(script)])

    # Nothing to do: every statement has already committed, and no connection stays open.
    def commit(self) -> None:
        pass

    def close(self) -> None:
        pass
