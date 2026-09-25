"""SQLite-backed append-only event ledger with a verifiable hash chain."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Ledger:
    def __init__(self, database: str = "procurement.db") -> None:
        self.database = database
        Path(database).parent.mkdir(parents=True, exist_ok=True) if Path(database).parent != Path(".") else None
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL UNIQUE
            )""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def append(self, event_type: str, entity_id: str, actor: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT event_hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
            previous_hash = previous["event_hash"] if previous else GENESIS
            timestamp = datetime.now(timezone.utc).isoformat()
            encoded = canonical(payload)
            material = canonical({"event_type": event_type, "entity_id": entity_id, "actor": actor,
                                  "payload": payload, "created_at": timestamp, "previous_hash": previous_hash})
            event_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
            cur = db.execute("INSERT INTO events(event_type,entity_id,actor,payload,created_at,previous_hash,event_hash) VALUES(?,?,?,?,?,?,?)",
                             (event_type, entity_id, actor, encoded, timestamp, previous_hash, event_hash))
            return {"id": cur.lastrowid, "type": event_type, "entity_id": entity_id, "actor": actor,
                    "payload": payload, "created_at": timestamp, "previous_hash": previous_hash, "hash": event_hash}

    def events(self, entity_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM events WHERE entity_id=? ORDER BY id" if entity_id else
                               "SELECT * FROM events ORDER BY id", (entity_id,) if entity_id else ()).fetchall()
        return [{"id": r["id"], "type": r["event_type"], "entity_id": r["entity_id"], "actor": r["actor"],
                 "payload": json.loads(r["payload"]), "created_at": r["created_at"],
                 "previous_hash": r["previous_hash"], "hash": r["event_hash"]} for r in rows]

    def verify(self) -> dict[str, Any]:
        events = self.events()
        previous_hash = GENESIS
        for event in events:
            material = canonical({"event_type": event["type"], "entity_id": event["entity_id"],
                                  "actor": event["actor"], "payload": event["payload"],
                                  "created_at": event["created_at"], "previous_hash": previous_hash})
            expected = hashlib.sha256(material.encode("utf-8")).hexdigest()
            if event["previous_hash"] != previous_hash or event["hash"] != expected:
                return {"valid": False, "checked_events": event["id"] - 1, "broken_event_id": event["id"]}
            previous_hash = event["hash"]
        return {"valid": True, "checked_events": len(events), "head": previous_hash}
