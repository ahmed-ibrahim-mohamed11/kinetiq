"""
Robust cross-platform singleton process guard for KinetiQ Edge AI.

Guarantees that Edge AI service never runs twice or competes for the camera.
Uses OS-level process locking (Win32 named mutex + msvcrt file locking on Windows,
fcntl.flock on Linux/macOS) with diagnostic metadata persistence and safe stale-lock recovery.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

# Windows Mutex Constants & Handles
MUTEX_NAME = "Local\\KinetiQ_Edge_AI_Singleton_Mutex"
ERROR_ALREADY_EXISTS = 183


class SingletonAlreadyRunningError(RuntimeError):
    """Raised when another valid Edge AI instance already holds the singleton lock."""

    def __init__(self, pid: int | None, health_url: str | None, metadata: dict[str, Any] | None = None) -> None:
        self.pid = pid
        self.health_url = health_url or "http://localhost:8088/healthz"
        self.metadata = metadata or {}
        msg = (
            f"EDGE_AI_ALREADY_RUNNING\n"
            f"Owning PID: {self.pid or 'Unknown'}\n"
            f"Health URL: {self.health_url}"
        )
        super().__init__(msg)


class SingletonLock:
    """OS-level singleton guard with diagnostic metadata."""

    def __init__(
        self,
        lock_file: Path | str,
        metadata_file: Path | str | None = None,
        camera_id: str | None = "default",
        health_port: int = 8088,
        version: str = "0.1.0",
        mutex_name: str = MUTEX_NAME,
    ) -> None:
        self.lock_file = Path(lock_file).resolve()
        self.metadata_file = (
            Path(metadata_file).resolve()
            if metadata_file
            else self.lock_file.with_suffix(".json")
        )
        self.camera_id = camera_id or "default"
        self.health_port = health_port
        self.version = version
        self.mutex_name = mutex_name

        self._file_handle: Any = None
        self._mutex_handle: Any = None
        self.acquired = False
        self.metadata: dict[str, Any] = {}

    def acquire(self) -> bool:
        """
        Atomically acquire OS-level singleton lock.
        If already owned by a live process:
          Prints EDGE_AI_ALREADY_RUNNING, prints PID and health URL, returns False.
        If metadata is stale but OS lock is free:
          Safely cleans stale metadata, acquires lock, writes fresh metadata, returns True.
        """
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self.metadata_file.parent.mkdir(parents=True, exist_ok=True)

        # 1. On Windows, attempt kernel named mutex acquisition
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # CreateMutexW(lpMutexAttributes, bInitialOwner, lpName)
            self._mutex_handle = kernel32.CreateMutexW(None, True, self.mutex_name)
            last_err = kernel32.GetLastError()
            if last_err == ERROR_ALREADY_EXISTS:
                # Another process already owns the mutex
                if self._mutex_handle:
                    kernel32.CloseHandle(self._mutex_handle)
                    self._mutex_handle = None
                return self._handle_conflict()

        # 2. Acquire OS file lock
        try:
            # Open file without truncating
            self._file_handle = open(self.lock_file, "a+b")
            self._file_handle.seek(0)

            if sys.platform == "win32":
                import msvcrt
                # Lock byte 0 non-blocking
                msvcrt.locking(self._file_handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        except (OSError, BlockingIOError, PermissionError):
            # OS lock held by another process
            if self._file_handle:
                try:
                    self._file_handle.close()
                except Exception:
                    pass
                self._file_handle = None
            if sys.platform == "win32" and self._mutex_handle:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
                self._mutex_handle = None
            return self._handle_conflict()

        # 3. Lock successfully acquired! Clean any stale metadata & write fresh metadata
        self.acquired = True
        self._write_metadata()
        return True

    def _handle_conflict(self) -> bool:
        """Read existing metadata, output conflict details, and refuse startup."""
        meta = self.read_metadata()
        pid = meta.get("pid")
        health_url = meta.get("health_url", f"http://localhost:{self.health_port}/healthz")

        print("EDGE_AI_ALREADY_RUNNING", file=sys.stderr)
        print(f"Owning PID: {pid or 'Unknown'}", file=sys.stderr)
        print(f"Health URL: {health_url}", file=sys.stderr)
        if meta.get("process_start_time"):
            print(f"Start Time: {meta.get('process_start_time')}", file=sys.stderr)
        if meta.get("command"):
            print(f"Command: {meta.get('command')}", file=sys.stderr)
        if meta.get("camera_id"):
            print(f"Camera ID: {meta.get('camera_id')}", file=sys.stderr)

        return False

    def _write_metadata(self) -> None:
        """Record diagnostic metadata beside the lock."""
        # Remove stale metadata first
        try:
            if self.metadata_file.exists():
                self.metadata_file.unlink(missing_ok=True)
        except Exception:
            pass

        start_time_iso = datetime.now(timezone.utc).isoformat()
        self.metadata = {
            "pid": os.getpid(),
            "process_start_time": start_time_iso,
            "hostname": socket.gethostname(),
            "command": " ".join(sys.argv),
            "edge_ai_version": self.version,
            "camera_id": self.camera_id,
            "health_url": f"http://localhost:{self.health_port}/healthz",
            "acquired_at_unix": time.time(),
        }

        # Write to temporary file then replace atomically
        tmp_meta = self.metadata_file.with_suffix(".tmp")
        try:
            with open(tmp_meta, "w", encoding="utf-8") as f:
                json.dump(self.metadata, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            tmp_meta.replace(self.metadata_file)
        except Exception:
            # Fallback direct write
            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(self.metadata, f, indent=2)

    def read_metadata(self) -> dict[str, Any]:
        """Read diagnostic metadata if available."""
        if not self.metadata_file.is_file():
            return {}
        try:
            with open(self.metadata_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def release(self) -> None:
        """Cleanly release lock and remove diagnostic metadata."""
        if not self.acquired:
            return

        # 1. Remove metadata file
        try:
            if self.metadata_file.exists():
                self.metadata_file.unlink(missing_ok=True)
        except Exception:
            pass

        # 2. Release OS file lock
        if self._file_handle is not None:
            try:
                self._file_handle.seek(0)
                if sys.platform == "win32":
                    import msvcrt
                    try:
                        msvcrt.locking(self._file_handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
                else:
                    import fcntl
                    try:
                        fcntl.flock(self._file_handle.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                self._file_handle.close()
            except Exception:
                pass
            self._file_handle = None

        # 3. Release Windows Mutex
        if sys.platform == "win32" and self._mutex_handle is not None:
            import ctypes
            try:
                ctypes.windll.kernel32.ReleaseMutex(self._mutex_handle)
                ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
            except Exception:
                pass
            self._mutex_handle = None

        # 4. Remove lock file
        try:
            if self.lock_file.exists():
                self.lock_file.unlink(missing_ok=True)
        except Exception:
            pass

        self.acquired = False

    def __enter__(self) -> SingletonLock:
        if not self.acquire():
            meta = self.read_metadata()
            raise SingletonAlreadyRunningError(
                pid=meta.get("pid"),
                health_url=meta.get("health_url"),
                metadata=meta,
            )
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()


def verify_edge_ai_process(pid: int, expected_start_time_iso: str | None = None) -> bool:
    """
    Safely verify if PID belongs to KinetiQ Edge AI.
    Never kills unrelated or reused Windows PIDs:
    Verifies process identity, executable name, command line, and start time.
    """
    if pid <= 0:
        return False
    try:
        proc = psutil.Process(pid)
        if not proc.is_running():
            return False

        # Name / Exe check: must be python / pythonw / edge-ai
        name = proc.name().lower()
        if not any(k in name for k in ("python", "edge_ai", "edge-ai")):
            return False

        # Command line check: must reference edge_ai or smartgym
        cmdline = " ".join(proc.cmdline()).lower()
        if not ("edge_ai" in cmdline or "smartgym" in cmdline or "verify_live_models" in cmdline):
            return False

        # Start time check if provided
        if expected_start_time_iso:
            try:
                exp_dt = datetime.fromisoformat(expected_start_time_iso.replace("Z", "+00:00"))
                proc_dt = datetime.fromtimestamp(proc.create_time(), tz=timezone.utc)
                # Allow up to 10 seconds difference due to clock/process spinup
                if abs((proc_dt - exp_dt).total_seconds()) > 10.0:
                    return False
            except Exception:
                pass

        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def safe_terminate_edge_ai(pid: int, expected_start_time_iso: str | None = None, timeout: float = 5.0) -> bool:
    """
    Safely terminate an Edge AI process ONLY if its identity is verified.
    Refuses to kill unrelated or reused PIDs.
    """
    if not verify_edge_ai_process(pid, expected_start_time_iso):
        return False

    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
            return True
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
            return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
