"""Python bridge to the C++ lock manager (src/cpp, `synapse_lock`).

Every lock decision (acquire, heartbeat, release, ownership checks) is made
by the C++ binary under an OS-level flock; this module only runs it and
translates its JSON output and exit codes.  Nothing here keeps lock state.

    SYNAPSE_LOCK_BIN      path to the binary (default: <repo>/src/cpp/build/synapse_lock)
    SYNAPSE_ENGINE_MODE   "wsl" (default on Windows) or "native"  -- shared with chunk_engine
    SYNAPSE_LOCK_LEASE    lease seconds, refreshed by heartbeats    (default 300 = 5 min)
    SYNAPSE_LOCK_ABANDON  seconds without heartbeat before another
                          user may reclaim the lock                (default 28800 = 8 h)
"""

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from src.py.chunk_engine import ENGINE_MODE, to_wsl_path
from src.py.store import BASE_DIR, LOCKS_DIR


LOCK_BIN = Path(os.getenv(
    "SYNAPSE_LOCK_BIN", BASE_DIR / "src" / "cpp" / "build" / "synapse_lock"
))
# Read at call time (module attributes) so tests can shorten them.
LEASE_SECONDS = int(os.getenv("SYNAPSE_LOCK_LEASE", "300"))
ABANDON_SECONDS = int(os.getenv("SYNAPSE_LOCK_ABANDON", str(8 * 60 * 60)))
TIMEOUT = 30

# Exit codes from lock_main.cpp
OK, ERROR, HELD, NOT_OWNER, NOT_LOCKED = 0, 1, 2, 3, 4


class LockError(RuntimeError):
    """The lock manager could not run or reported an internal error."""


@dataclass
class LockReply:
    code: int             # exit code (OK / HELD / NOT_OWNER / NOT_LOCKED)
    status: str           # "granted", "renewed", "reclaimed", "held_by_other", ...
    lock: dict | None     # current lock record (after the operation)
    previous: dict | None # reclaimed or released record
    now: int

    @property
    def ok(self) -> bool:
        return self.code == OK


def _arg_path(path: Path) -> str:
    return to_wsl_path(path) if ENGINE_MODE == "wsl" else str(Path(path).resolve())


def _run(args: list[str]) -> dict:
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    binary = _arg_path(LOCK_BIN)
    command = ["wsl.exe", "-e", binary, *args] if ENGINE_MODE == "wsl" else [binary, *args]
    try:
        result = subprocess.run(command, capture_output=True, timeout=TIMEOUT, check=False)
    except FileNotFoundError as exc:
        raise LockError("Could not start the lock manager.") from exc
    except subprocess.TimeoutExpired as exc:
        raise LockError("The lock manager timed out.") from exc

    stdout = result.stdout.decode("utf-8", errors="replace").strip()
    if result.returncode == ERROR or not stdout:
        message = result.stderr.decode("utf-8", errors="replace").strip() or stdout
        try:
            message = json.loads(message).get("message", message)
        except (ValueError, AttributeError):
            pass
        raise LockError(f"Lock manager error: {message or f'exit {result.returncode}'}")

    try:
        data = json.loads(stdout)
    except ValueError as exc:
        raise LockError(f"Lock manager returned invalid JSON: {stdout[:200]!r}") from exc
    data["_code"] = result.returncode
    return data


def _reply(data: dict) -> LockReply:
    return LockReply(
        code=data["_code"],
        status=data.get("status", ""),
        lock=data.get("lock"),
        previous=data.get("previous"),
        now=data.get("now", 0),
    )


def acquire(file_id: int, user_id: int, session_id: str) -> LockReply:
    return _reply(_run([
        "acquire", _arg_path(LOCKS_DIR), str(file_id), str(user_id), session_id,
        str(LEASE_SECONDS), str(ABANDON_SECONDS),
    ]))


def heartbeat(file_id: int, user_id: int, session_id: str) -> LockReply:
    return _reply(_run([
        "heartbeat", _arg_path(LOCKS_DIR), str(file_id), str(user_id), session_id,
        str(LEASE_SECONDS),
    ]))


def release(file_id: int, user_id: int, session_id: str) -> LockReply:
    return _reply(_run([
        "release", _arg_path(LOCKS_DIR), str(file_id), str(user_id), session_id,
    ]))


def status(file_id: int) -> dict | None:
    """Current lock record for a file, or None if it is not locked."""
    return _run(["status", _arg_path(LOCKS_DIR), str(file_id)]).get("lock")


def list_locks() -> dict[int, dict]:
    """All current locks keyed by file id. A corrupt record counts as locked."""
    data = _run(["list", _arg_path(LOCKS_DIR)])
    locks = {int(r["file_id"]): r for r in data.get("locks", [])}
    for file_id in data.get("corrupt", []):
        locks[int(file_id)] = {"file_id": int(file_id), "user_id": None, "state": "corrupt"}
    return locks
