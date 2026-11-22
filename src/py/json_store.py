"""JSON-file storage backend for the development phase.

Each table from database/schema.sql is stored as its own JSON file in the
data directory:

    data/users.json, data/files.json, data/file_chunks.json,
    data/permissions.json, data/activity_logs.json

File format:  {"next_id": <int>, "rows": [ {...}, ... ]}

Rows use the same column names as schema.sql so that moving back to
PostgreSQL later is a straight mapping.  `edit_sessions.json` is an extra
table for desktop editing sessions (the lock itself lives in the C++ lock
manager, never here).

Concurrency: every operation holds a re-entrant thread lock AND an
operating-system file lock on data/.store.lock, so threads and separate
processes (several uvicorn workers) are serialised.  Every write goes to a
temp file first and is swapped in with os.replace(), so a crash never
leaves a half-written table.
"""

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

if os.name == "nt":
    import msvcrt
else:
    import fcntl


TABLES = (
    "users", "files", "file_chunks", "permissions", "activity_logs",
    "edit_sessions", "stars",
)
PERMISSIONS = ("read", "write")


class DuplicateError(Exception):
    """Raised when a unique constraint would be violated.

    `field` names the column that clashed (e.g. "username", "email",
    "file_name").
    """

    def __init__(self, field: str):
        super().__init__(f"duplicate {field}")
        self.field = field


class VersionConflict(Exception):
    """The file changed since the caller read it (expected version is stale)."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON to `path` atomically (temp file in same dir + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(data, fp, indent=2, ensure_ascii=False)
            fp.flush()
            os.fsync(fp.fileno())

        # On Windows os.replace can fail briefly if another program
        # (antivirus, an editor) has the target open, so retry a few times.
        for attempt in range(5):
            try:
                os.replace(tmp_name, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


class _StoreLock:
    """Re-entrant within a process, exclusive across processes.

    The OS lock is taken only by the outermost `with` in a thread, so store
    methods can call each other freely.
    """

    def __init__(self, path: Path, timeout: float = 10.0):
        self._rlock = RLock()
        self._depth = 0
        self._timeout = timeout
        self._fh = open(path, "a+b")

    def _os_lock(self) -> None:
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                self._fh.seek(0)
                if os.name == "nt":
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for the JSON store lock.")
                time.sleep(0.005)

    def _os_unlock(self) -> None:
        self._fh.seek(0)
        if os.name == "nt":
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)

    def __enter__(self):
        self._rlock.acquire()
        if self._depth == 0:
            try:
                self._os_lock()
            except BaseException:
                self._rlock.release()
                raise
        self._depth += 1
        return self

    def __exit__(self, *exc):
        self._depth -= 1
        if self._depth == 0:
            self._os_unlock()
        self._rlock.release()
        return False

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class JSONStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = _StoreLock(self.data_dir / ".store.lock")

    def close(self) -> None:
        """Release the handle on the store's lock file."""
        self._lock.close()

    # ------------------------------------------------------------------
    # Low-level table access
    # ------------------------------------------------------------------
    def _path(self, table: str) -> Path:
        if table not in TABLES:
            raise ValueError(f"Unknown table: {table}")
        return self.data_dir / f"{table}.json"

    def _load(self, table: str) -> dict:
        path = self._path(table)
        if not path.exists():
            return {"next_id": 1, "rows": []}
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        data.setdefault("next_id", 1)
        data.setdefault("rows", [])
        return data

    def _save(self, table: str, data: dict) -> None:
        atomic_write_json(self._path(table), data)

    @staticmethod
    def _insert(data: dict, row: dict) -> dict:
        row = {"id": data["next_id"], **row}
        data["next_id"] += 1
        data["rows"].append(row)
        return row

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------
    def find_existing_user(self, username: str, email: str) -> str | None:
        """Return "username" or "email" if either is taken, else None."""
        username_key = username.casefold()
        email_key = email.casefold()
        with self._lock:
            for user in self._load("users")["rows"]:
                if user["username"].casefold() == username_key:
                    return "username"
                if user["email"].casefold() == email_key:
                    return "email"
        return None

    def create_user(self, username: str, email: str, password_hash: str) -> dict:
        with self._lock:
            duplicate = self.find_existing_user(username, email)
            if duplicate:
                raise DuplicateError(duplicate)

            data = self._load("users")
            row = self._insert(data, {
                "username": username,
                "email": email,
                "password_hash": password_hash,
                "created_at": utc_now(),
            })
            self._save("users", data)
            return dict(row)

    def get_user(self, user_id: int) -> dict | None:
        """Public user fields only (never the password hash)."""
        with self._lock:
            for user in self._load("users")["rows"]:
                if user["id"] == user_id:
                    return {
                        "id": user["id"],
                        "username": user["username"],
                        "email": user["email"],
                    }
        return None

    def find_user_by_username(self, username: str) -> dict | None:
        key = username.strip().casefold()
        with self._lock:
            for user in self._load("users")["rows"]:
                if user["username"].casefold() == key:
                    return {
                        "id": user["id"],
                        "username": user["username"],
                        "email": user["email"],
                    }
        return None

    def find_user_by_email(self, email: str) -> dict | None:
        email_key = email.strip().casefold()
        with self._lock:
            for user in self._load("users")["rows"]:
                if user["email"].casefold() == email_key:
                    return {
                        "id": user["id"],
                        "username": user["username"],
                        "email": user["email"],
                    }
        return None

    # ------------------------------------------------------------------
    # Files and chunks
    # ------------------------------------------------------------------
    def file_name_exists(self, owner_id: int, file_name: str) -> bool:
        key = file_name.casefold()
        with self._lock:
            return any(
                row["owner_id"] == owner_id and row["file_name"].casefold() == key
                for row in self._load("files")["rows"]
            )

    def create_file(
        self,
        owner_id: int,
        file_name: str,
        file_size: int,
        mime_type: str | None,
        chunks: list[dict] | None = None,
    ) -> dict:
        """Create a file row (and its chunk rows) after a duplicate-name check.

        `chunks` items: {"chunk_index", "chunk_hash", "chunk_size", "storage_path"}.
        The check and both writes happen under one lock, so two uploads with
        the same name cannot both succeed.
        """
        with self._lock:
            if self.file_name_exists(owner_id, file_name):
                raise DuplicateError("file_name")

            files = self._load("files")
            now = utc_now()
            row = self._insert(files, {
                "owner_id": owner_id,
                "file_name": file_name,
                "file_size": file_size,
                "mime_type": mime_type,
                "version": 1,
                "created_at": now,
                "updated_at": now,
            })

            if chunks:
                chunk_table = self._load("file_chunks")
                for chunk in chunks:
                    self._insert(chunk_table, {
                        "file_id": row["id"],
                        "version": 1,
                        "chunk_index": chunk["chunk_index"],
                        "chunk_hash": chunk["chunk_hash"],
                        "chunk_size": chunk["chunk_size"],
                        "storage_path": chunk["storage_path"],
                    })
                # Write chunks first: if the process dies between the two
                # writes we get orphan chunk rows, never a file row whose
                # chunks are missing.
                self._save("file_chunks", chunk_table)

            self._save("files", files)
            return dict(row)

    def list_files(self, owner_id: int) -> list[dict]:
        with self._lock:
            rows = [
                dict(row) for row in self._load("files")["rows"]
                if row["owner_id"] == owner_id
            ]
        rows.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
        return rows

    def get_file(self, file_id: int) -> dict | None:
        with self._lock:
            for row in self._load("files")["rows"]:
                if row["id"] == file_id:
                    return dict(row)
        return None

    def get_file_chunks(self, file_id: int, version: int | None = None) -> list[dict]:
        """Chunks of one version of a file (default: its current version).

        Rows written before versioning existed have no "version" and count
        as version 1.
        """
        with self._lock:
            if version is None:
                row = self.get_file(file_id)
                version = int(row.get("version", 1)) if row else 1
            rows = [
                dict(r) for r in self._load("file_chunks")["rows"]
                if r["file_id"] == file_id and int(r.get("version", 1)) == version
            ]
        rows.sort(key=lambda r: r["chunk_index"])
        return rows

    def replace_file_content(
        self,
        file_id: int,
        expected_version: int,
        file_size: int,
        chunks: list[dict],
    ) -> dict:
        """Make `chunks` the new current version of a file.

        Atomic: the new chunk rows are written first under the next version
        number (invisible to readers, who follow files.version), then the
        file row's version is switched in a single atomic write. If anything
        fails before that write, the file still points at its old chunks,
        which are never modified or deleted (content-addressed storage), so
        the original stays intact. Old versions remain as history.

        Raises VersionConflict if the file is no longer at `expected_version`.
        """
        with self._lock:
            files = self._load("files")
            row = next((r for r in files["rows"] if r["id"] == file_id), None)
            if row is None:
                raise KeyError(file_id)
            current = int(row.get("version", 1))
            if current != expected_version:
                raise VersionConflict(f"file {file_id} is at version {current}")
            new_version = current + 1

            chunk_table = self._load("file_chunks")
            # Drop leftovers of an earlier attempt that died before its commit.
            chunk_table["rows"] = [
                r for r in chunk_table["rows"]
                if not (r["file_id"] == file_id and int(r.get("version", 1)) == new_version)
            ]
            for chunk in chunks:
                self._insert(chunk_table, {
                    "file_id": file_id,
                    "version": new_version,
                    "chunk_index": chunk["chunk_index"],
                    "chunk_hash": chunk["chunk_hash"],
                    "chunk_size": chunk["chunk_size"],
                    "storage_path": chunk["storage_path"],
                })
            self._save("file_chunks", chunk_table)

            # Commit point: one atomic write of files.json.
            row["version"] = new_version
            row["file_size"] = file_size
            row["updated_at"] = utc_now()
            self._save("files", files)
            return dict(row)

    def set_trashed(self, file_id: int, trashed: bool) -> dict | None:
        """Move a file to Trash (soft delete) or restore it. Contents and
        chunks are kept; there is no permanent delete in this version."""
        with self._lock:
            files = self._load("files")
            for row in files["rows"]:
                if row["id"] == file_id:
                    row["trashed_at"] = utc_now() if trashed else None
                    self._save("files", files)
                    return dict(row)
        return None

    # ------------------------------------------------------------------
    # Stars (per user)
    # ------------------------------------------------------------------
    def set_star(self, user_id: int, file_id: int, starred: bool) -> None:
        with self._lock:
            data = self._load("stars")
            exists = any(r["user_id"] == user_id and r["file_id"] == file_id for r in data["rows"])
            if starred and not exists:
                self._insert(data, {"user_id": user_id, "file_id": file_id, "created_at": utc_now()})
            elif not starred and exists:
                data["rows"] = [
                    r for r in data["rows"]
                    if not (r["user_id"] == user_id and r["file_id"] == file_id)
                ]
            else:
                return
            self._save("stars", data)

    def starred_ids(self, user_id: int) -> set[int]:
        with self._lock:
            return {r["file_id"] for r in self._load("stars")["rows"] if r["user_id"] == user_id}

    # ------------------------------------------------------------------
    # Permissions (sharing)  -- one row per (file_id, user_id)
    # ------------------------------------------------------------------
    def get_permission(self, file_id: int, user_id: int) -> str | None:
        with self._lock:
            for row in self._load("permissions")["rows"]:
                if row["file_id"] == file_id and row["user_id"] == user_id:
                    return row["permission"]
        return None

    def set_permission(self, file_id: int, user_id: int, permission: str) -> dict:
        if permission not in PERMISSIONS:
            raise ValueError(f"permission must be one of {PERMISSIONS}")
        with self._lock:
            data = self._load("permissions")
            for row in data["rows"]:
                if row["file_id"] == file_id and row["user_id"] == user_id:
                    row["permission"] = permission
                    self._save("permissions", data)
                    return dict(row)
            row = self._insert(data, {
                "file_id": file_id,
                "user_id": user_id,
                "permission": permission,
                "created_at": utc_now(),
            })
            self._save("permissions", data)
            return dict(row)

    def remove_permission(self, file_id: int, user_id: int) -> bool:
        with self._lock:
            data = self._load("permissions")
            before = len(data["rows"])
            data["rows"] = [
                r for r in data["rows"]
                if not (r["file_id"] == file_id and r["user_id"] == user_id)
            ]
            if len(data["rows"]) == before:
                return False
            self._save("permissions", data)
            return True

    def list_permissions(self, file_id: int) -> list[dict]:
        with self._lock:
            return [
                dict(r) for r in self._load("permissions")["rows"]
                if r["file_id"] == file_id
            ]

    def list_shared_with(self, user_id: int) -> list[dict]:
        """Files shared with `user_id`, each with an extra "permission" key."""
        with self._lock:
            granted = {
                r["file_id"]: r["permission"]
                for r in self._load("permissions")["rows"]
                if r["user_id"] == user_id
            }
            rows = [
                {**row, "permission": granted[row["id"]]}
                for row in self._load("files")["rows"]
                if row["id"] in granted and row["owner_id"] != user_id
            ]
        rows.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
        return rows

    # ------------------------------------------------------------------
    # Desktop editing sessions (metadata only; the lock is in C++)
    # ------------------------------------------------------------------
    def create_edit_session(
        self, session_id: str, file_id: int, user_id: int, base_version: int
    ) -> dict:
        with self._lock:
            data = self._load("edit_sessions")
            now = utc_now()
            row = self._insert(data, {
                "session_id": session_id,
                "file_id": file_id,
                "user_id": user_id,
                "base_version": base_version,
                "status": "active",
                "created_at": now,
                "last_heartbeat": now,
                "closed_at": None,
            })
            self._save("edit_sessions", data)
            return dict(row)

    def get_edit_session(self, session_id: str) -> dict | None:
        with self._lock:
            for row in self._load("edit_sessions")["rows"]:
                if row["session_id"] == session_id:
                    return dict(row)
        return None

    def update_edit_session(self, session_id: str, **fields) -> dict | None:
        with self._lock:
            data = self._load("edit_sessions")
            for row in data["rows"]:
                if row["session_id"] == session_id:
                    row.update(fields)
                    self._save("edit_sessions", data)
                    return dict(row)
        return None

    def list_edit_sessions(
        self,
        user_id: int | None = None,
        file_id: int | None = None,
        status: str | None = "active",
    ) -> list[dict]:
        with self._lock:
            return [
                dict(r) for r in self._load("edit_sessions")["rows"]
                if (user_id is None or r["user_id"] == user_id)
                and (file_id is None or r["file_id"] == file_id)
                and (status is None or r["status"] == status)
            ]

    # ------------------------------------------------------------------
    # Activity log
    # ------------------------------------------------------------------
    def log_activity(
        self,
        user_id: int | None,
        file_id: int | None,
        action: str,
        details: dict | None = None,
    ) -> None:
        with self._lock:
            data = self._load("activity_logs")
            self._insert(data, {
                "user_id": user_id,
                "file_id": file_id,
                "action": action,
                "details": details,
                "created_at": utc_now(),
            })
            self._save("activity_logs", data)
