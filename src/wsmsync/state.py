"""SQLite ledger of what was already sent, so every run is idempotent."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class State:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS synced ("
            " profile TEXT, ws_id TEXT, monarch_id TEXT, date TEXT, amount REAL,"
            " synced_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (profile, ws_id))"
        )
        self.db.commit()

    def seen(self, profile: str, ws_id: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM synced WHERE profile=? AND ws_id=?", (profile, ws_id)
        ).fetchone() is not None

    def record(self, profile: str, ws_id: str, monarch_id: str, date: str, amount: float) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO synced (profile, ws_id, monarch_id, date, amount) VALUES (?,?,?,?,?)",
            (profile, ws_id, monarch_id, date, amount),
        )
        self.db.commit()
