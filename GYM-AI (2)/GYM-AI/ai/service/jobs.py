"""In-memory analysis jobs: ONE worker thread (model instances are never shared across threads),
a bounded queue, TTL eviction of finished jobs, and the uploaded video deleted as soon as its job
finishes (unless explicitly configured otherwise). Logs carry job ids only — never file names."""
from __future__ import annotations

import dataclasses
import logging
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from ai.core.schema import AnalysisResult

log = logging.getLogger("gym_ai.service")
INTERNAL_ERROR = "internal error while analysing the video"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


@dataclass
class Job:
    id: str
    upload_path: str
    created_at: datetime
    state: str = "queued"                  # queued | running | succeeded | failed
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    result: Optional[AnalysisResult] = None


class QueueFull(Exception):
    pass


class JobRunning(Exception):
    pass


class JobManager:
    def __init__(self, analyzer: Callable[[str], AnalysisResult], max_pending: int = 4,
                 ttl_s: float = 3600.0, keep_uploads: bool = False):
        self._analyzer = analyzer
        self._ttl_s = ttl_s
        self._keep_uploads = keep_uploads
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._q: "queue.Queue[Optional[str]]" = queue.Queue(maxsize=max_pending)
        self._thread = threading.Thread(target=self._run, name="gym-ai-analysis-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 30.0) -> None:
        """Stop after the job in progress; drop queued jobs and their uploads."""
        with self._lock:
            pending = [j for j in self._jobs.values() if j.state == "queued"]
            for j in pending:
                del self._jobs[j.id]
                _remove(j.upload_path)
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._q.put(None)
        self._thread.join(timeout)

    def submit(self, upload_path: str) -> Job:
        self._evict()
        job = Job(id=uuid.uuid4().hex, upload_path=upload_path, created_at=_now())
        with self._lock:
            self._jobs[job.id] = job
        try:
            self._q.put_nowait(job.id)
        except queue.Full:
            with self._lock:
                del self._jobs[job.id]
            raise QueueFull()
        log.info("job %s queued", job.id)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        """A consistent snapshot (the worker mutates the live record under the lock)."""
        self._evict()
        with self._lock:
            job = self._jobs.get(job_id)
            return dataclasses.replace(job) if job is not None else None

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.state == "running":
                raise JobRunning()
            del self._jobs[job_id]
        _remove(job.upload_path)
        log.info("job %s deleted", job_id)
        return True

    def queue_depth(self) -> int:
        return self._q.qsize()

    def _evict(self) -> None:
        cutoff = time.time() - self._ttl_s
        with self._lock:
            old = [j.id for j in self._jobs.values()
                   if j.finished_at is not None and j.finished_at.timestamp() < cutoff]
            for jid in old:
                del self._jobs[jid]

    def _run(self) -> None:
        while True:
            jid = self._q.get()
            if jid is None:
                return
            with self._lock:
                job = self._jobs.get(jid)
                if job is None:                    # deleted while queued
                    continue
                job.state, job.started_at = "running", _now()
            t0 = time.perf_counter()
            result, error = None, None
            try:
                result = self._analyzer(job.upload_path)
            except (FileNotFoundError, ValueError) as e:
                error = str(e).replace(job.upload_path, "the uploaded video")
            except Exception:
                log.exception("job %s: internal error", jid)
                error = INTERNAL_ERROR
            finally:
                if not self._keep_uploads:
                    _remove(job.upload_path)
            with self._lock:
                job.result, job.error = result, error
                job.state = "succeeded" if error is None else "failed"
                job.finished_at = _now()
            log.info("job %s %s in %.1f s", jid, job.state, time.perf_counter() - t0)
