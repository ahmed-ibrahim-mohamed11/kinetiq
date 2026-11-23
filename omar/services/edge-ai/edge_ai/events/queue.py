"""
Durable, ordered, at-least-once event queue on SQLite (WAL mode, synchronous=FULL
so a committed event survives a power cut, not just a process crash).

* `append` assigns the monotonic per-device `sequence` inside the same
  transaction that persists the event, so sequence numbers survive restarts and
  never repeat.
* `peek_batch` returns the oldest N unsent events in strict sequence order. The
  cloud resolves children (set → rep) through their parents, so nothing newer
  may overtake an event that is backing off: while the HEAD of the queue is
  deferred, nothing is returned at all.
* `ack` deletes events after the cloud confirmed the batch; `nack` records the
  failure and backs off; `drop` moves permanently rejected events to a small
  `dead_letter` table (kept 30 days) for operators to inspect.
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
        # FULL: in WAL mode NORMAL does not fsync on commit, so events committed since the last
        # checkpoint could roll back on power loss. The write rate is a few events per second.
        self._db.execute("pragma synchronous=full")
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
            create table if not exists dead_letter (
              event_id text primary key,
              sequence integer not null,
              event_type text not null,
              body text not null,
              code text,
              reason text,
              dropped_at real not null
            );
            """
        )
        self._db.execute("insert or ignore into meta(key, value) values ('next_sequence', '0')")

    # ── write side ───────────────────────────────────────────────────────────
    def next_sequence(self) -> int:
        with self._lock:
            return int(self._db.execute("select value from meta where key='next_sequence'").fetchone()[0])

    def append(self, build: Callable[[int], dict[str, Any]], max_depth: int = 5000) -> dict[str, Any]:
        """`build(sequence) -> envelope dict`. Runs in one transaction so the sequence is never skipped."""
        with self._lock:
            self._db.execute("begin immediate")
            try:
                cur_count = int(self._db.execute("select count(*) from events").fetchone()[0])
                if cur_count >= max_depth:
                    ephemeral = self._db.execute(
                        "select event_id, sequence, event_type, body from events "
                        "where event_type in ('tracking.uncertain', 'device.heartbeat') "
                        "order by sequence asc limit 50"
                    ).fetchall()
                    now = time.time()
                    for ev in ephemeral:
                        self._db.execute(
                            "insert or replace into dead_letter "
                            "(event_id, sequence, event_type, body, code, reason, dropped_at) values (?,?,?,?,?,?,?)",
                            (ev[0], ev[1], ev[2], ev[3], "queue_overflow", "buffer pruned during outage", now),
                        )
                        self._db.execute("delete from events where event_id = ?", (ev[0],))

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
        """Oldest `limit` events in sequence order, or [] while the head event is backing off."""
        now = now if now is not None else time.time()
        with self._lock:
            head = self._db.execute("select not_before from events order by sequence asc limit 1").fetchone()
            if head is None or float(head[0]) > now:
                return []
            rows = self._db.execute("select body from events order by sequence asc limit ?", (limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def pending_payloads(self, event_type: str) -> list[dict[str, Any]]:
        """Payloads of queued (not yet acknowledged) events of one type."""
        with self._lock:
            rows = self._db.execute("select body from events where event_type = ?", (event_type,)).fetchall()
        return [json.loads(r[0]).get("payload", {}) for r in rows]

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

    def drop(
        self,
        event_ids: list[str],
        code: str | None = None,
        reasons: dict[str, tuple[str | None, str | None]] | None = None,
    ) -> int:
        """Remove events that must never be retried (rejected by the cloud, poison batches) from the
        queue, keeping a copy in `dead_letter`. `reasons` maps event_id -> (code, reason)."""
        if not event_ids:
            return 0
        now = time.time()
        with self._lock:
            self._db.execute("begin")
            total = 0
            for event_id in event_ids:
                row = self._db.execute(
                    "select sequence, event_type, body from events where event_id = ?", (event_id,)
                ).fetchone()
                if row is None:
                    continue
                ev_code, ev_reason = (reasons or {}).get(event_id, (code, None))
                self._db.execute(
                    "insert or replace into dead_letter"
                    "(event_id, sequence, event_type, body, code, reason, dropped_at) values (?,?,?,?,?,?,?)",
                    (event_id, row[0], row[1], row[2], ev_code, (ev_reason or "")[:500] or None, now),
                )
                total += self._db.execute("delete from events where event_id = ?", (event_id,)).rowcount
            # Bounded retention: the dead letter holds member ids, so it is not an archive.
            self._db.execute("delete from dead_letter where dropped_at < ?", (now - 30 * 86400,))
            self._db.execute("commit")
            return total

    def dead_letter_depth(self) -> int:
        with self._lock:
            return int(self._db.execute("select count(*) from dead_letter").fetchone()[0])

    def dead_letters(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "select event_id, event_type, code, reason, dropped_at from dead_letter "
                "order by dropped_at desc limit ?",
                (limit,),
            ).fetchall()
        return [{"event_id": r[0], "event_type": r[1], "code": r[2], "reason": r[3], "dropped_at": r[4]} for r in rows]

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
