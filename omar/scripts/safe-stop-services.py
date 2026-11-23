"""
Safe service shutdown tool for KinetiQ development environment.
Safely terminates Edge AI, Admin Web, and Member Web without killing unrelated PIDs.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

import psutil

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "edge-ai"))

try:
    from edge_ai.singleton import safe_terminate_edge_ai, verify_edge_ai_process
except ImportError:
    def verify_edge_ai_process(pid: int, expected_start_time_iso: str | None = None) -> bool:
        try:
            p = psutil.Process(pid)
            cmd = " ".join(p.cmdline()).lower()
            return "edge_ai" in cmd or "edge-ai" in cmd
        except Exception:
            return False

    def safe_terminate_edge_ai(pid: int, expected_start_time_iso: str | None = None, timeout: float = 3.0) -> bool:
        if not verify_edge_ai_process(pid, expected_start_time_iso):
            return False
        try:
            p = psutil.Process(pid)
            p.terminate()
            p.wait(timeout=timeout)
            return True
        except Exception:
            return False


def get_pids_for_port(port: int) -> set[int]:
    pids = set()
    for conn in psutil.net_connections(kind="inet"):
        if conn.laddr.port == port and conn.status == "LISTEN" and conn.pid:
            pids.add(conn.pid)
    return pids


def stop_port(port: int, service_name: str, allowed_executables: tuple[str, ...]) -> None:
    pids = get_pids_for_port(port)
    if not pids:
        print(f"[*] {service_name} (Port {port}): No active process found.")
        return

    for pid in pids:
        try:
            proc = psutil.Process(pid)
            proc_name = proc.name().lower()
            if not any(ae in proc_name for ae in allowed_executables):
                print(f"[!] Refusing to kill PID {pid} on port {port}: executable '{proc_name}' does not match {allowed_executables}.")
                continue

            print(f"[*] Terminating {service_name} (PID: {pid}, exe: {proc.name()})...")
            # If edge_ai, use the verified terminator
            if port == 8088:
                if safe_terminate_edge_ai(pid):
                    print(f"  ✓ {service_name} PID {pid} stopped safely.")
                else:
                    print(f"  [!] Edge AI verification failed for PID {pid}; not terminating.")
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=4.0)
                    print(f"  ✓ {service_name} PID {pid} stopped safely.")
                except psutil.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2.0)
                    print(f"  ✓ {service_name} PID {pid} killed after timeout.")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            print(f"  [!] PID {pid}: {exc}")


def main() -> int:
    print("=======================================================================")
    print(" KinetiQ Safe Shutdown Tool")
    print("=======================================================================")

    # 1. Stop Edge AI (Port 8088)
    stop_port(8088, "Edge AI Vision Service", ("python", "edge-ai", "edge_ai"))

    # Also clean lockfile if stale
    lock_file = REPO_ROOT / "services" / "edge-ai" / "data" / "edge_ai.lock"
    meta_file = REPO_ROOT / "services" / "edge-ai" / "data" / "edge_ai_singleton.json"
    if lock_file.exists():
        try:
            lock_file.unlink(missing_ok=True)
        except Exception:
            pass
    if meta_file.exists():
        try:
            meta_file.unlink(missing_ok=True)
        except Exception:
            pass

    # 2. Stop Admin Web (Port 3000)
    stop_port(3000, "Admin Web", ("node", "next"))

    # 3. Stop Member Web (Port 3001)
    stop_port(3001, "Member Web", ("node", "next"))

    # Verify ports are clear
    time.sleep(1.0)
    remaining_ports = []
    for port in (8088, 3000, 3001):
        pids = get_pids_for_port(port)
        if pids:
            remaining_ports.append((port, pids))

    if remaining_ports:
        print(f"[!] Warning: Following ports still occupied: {remaining_ports}")
        return 1
    else:
        print("[*] All KinetiQ services successfully stopped and ports freed.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
