"""
Durable, ordered, at-least-once event queue on SQLite (WAL mode).

* `append` assigns the monotonic per-device `sequence` inside the same
  transaction that persists the event, so sequence numbers survive restarts and
  never repeat.
* `peek_batch` returns the oldest N unsent events; `ack` deletes them after the
  cloud confirmed the batch; `nack` records the failure and backs off.
* The cloud dedupes on event_id, so re-sending after a crash between POST and
  ack is safe (at-least-once → effectively-once).
* `depth()` feeds the heartbeat's queue_depth and the health endpoint.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


class PersistentEventQueue:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._db.execute("pragma journal_mode=wal")
        self._db.execute("pragma synchronous=normal")
        self._db.executescript(
            """
            create table if not exists meta (key text primary key, value text not null);
            create table if not exists events (
              sequence integer primary key,
              event_id text not null unique,
              event_type text not null,
              occurred_at text not null,
              body text not null,
              enqueued_at real not null,
              attempts integer not null default 0,
              last_error text,
              not_before real not null default 0
            );
            create index if not exists idx_events_not_before on events(not_before);
            """
        )
        self._db.execute("insert or ignore into meta(key, value) values ('next_sequence', '0')")

    # ── write side ───────────────────────────────────────────────────────────
    def next_sequence(self) -> int:
        with self._lock:
            return int(self._db.execute("select value from meta where key='next_sequence'").fetchone()[0])

    def append(self, build: Callable[[int], dict[str, Any]]) -> dict[str, Any]:
        """`build(sequence) -> envelope dict`. Runs in one transaction so the sequence is never skipped."""
        with self._lock:
            self._db.execute("begin immediate")
            try:
                seq = int(self._db.execute("select value from meta where key='next_sequence'").fetchone()[0])
                envelope = build(seq)
                assert envelope["sequence"] == seq
                self._db.execute(
                    "insert into events(sequence, event_id, event_type, occurred_at, body, enqueued_at) "
                    "values (?,?,?,?,?,?)",
                    (
                        seq,
                        envelope["event_id"],
                        envelope["event_type"],
                        envelope["occurred_at"],
                        json.dumps(envelope, separators=(",", ":")),
                        time.time(),
                    ),
                )
                self._db.execute("update meta set value=? where key='next_sequence'", (str(seq + 1),))
                self._db.execute("commit")
                return envelope
            except Exception:
                self._db.execute("rollback")
                raise

    # ── read side ────────────────────────────────────────────────────────────
    def peek_batch(self, limit: int, now: float | None = None) -> list[dict[str, Any]]:
        now = now if now is not None else time.time()
        with self._lock:
            rows = self._db.execute(
                "select body from events where not_before <= ? order by sequence asc limit ?", (now, limit)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def ack(self, event_ids: list[str]) -> int:
        if not event_ids:
            return 0
        with self._lock:
            self._db.execute("begin")
            total = 0
            for i in range(0, len(event_ids), 500):
                chunk = event_ids[i : i + 500]
                placeholders = ",".join("?" * len(chunk))  # only '?' markers are interpolated; values are bound
                cur = self._db.execute(f"delete from events where event_id in ({placeholders})", chunk)  # noqa: S608
                total += cur.rowcount
            self._db.execute("commit")
            return total

    def nack(self, event_ids: list[str], error: str, backoff_seconds: float) -> None:
        if not event_ids:
            return
        with self._lock:
            self._db.execute("begin")
            for i in range(0, len(event_ids), 500):
                chunk = event_ids[i : i + 500]
                placeholders = ",".join("?" * len(chunk))  # only '?' markers are interpolated; values are bound
                sql = f"update events set attempts = attempts + 1, last_error = ?, not_before = ? where event_id in ({placeholders})"  # noqa: S608, E501
                self._db.execute(sql, [error[:500], time.time() + backoff_seconds, *chunk])
            self._db.execute("commit")

    def drop(self, event_ids: list[str]) -> int:
        """Permanently discard events the server rejected as invalid (never retry poison messages)."""
        return self.ack(event_ids)

    def depth(self) -> int:
        with self._lock:
            return int(self._db.execute("select count(*) from events").fetchone()[0])

    def oldest_age_seconds(self) -> float | None:
        with self._lock:
            row = self._db.execute("select min(enqueued_at) from events").fetchone()
        return None if row is None or row[0] is None else time.time() - float(row[0])

    def close(self) -> None:
        with self._lock:
            self._db.close()
