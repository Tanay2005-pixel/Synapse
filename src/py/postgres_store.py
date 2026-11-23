"""PostgreSQL storage backend (same interface as json_store.JSONStore).

Selected with SYNAPSE_STORAGE=postgres. Uses the existing
src/py/db_connection.py and database/schema.sql unchanged.

The user/file/activity queries are the ones that previously lived in
registration.py, user_login.py and workspace.py. The chunk queries are new.
NOTE: this backend has not been run against a live database since the
JSON migration -- test it before relying on it.
"""

import json

from src.py.json_store import DuplicateError, VersionConflict


def _connect():
    # Imported lazily so the JSON backend works without psycopg / DB_PASSWORD.
    from src.py.db_connection import get_db_connection
    return get_db_connection()


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value



_FILE_COLUMNS = """
    id, owner_id, file_name, file_size, mime_type,
    created_at, updated_at, version, trashed_at
"""


def _file_row(row) -> dict:
    return {
        "id": row[0],
        "owner_id": row[1],
        "file_name": row[2],
        "file_size": row[3],
        "mime_type": row[4],
        "created_at": _iso(row[5]),
        "updated_at": _iso(row[6]),
        "version": row[7],
        "trashed_at": _iso(row[8]),
    }


class PostgresStore:
    def find_existing_user(self, username: str, email: str) -> str | None:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT
                    CASE
                        WHEN LOWER(username) = LOWER(%s)
                            THEN 'username'
                        WHEN LOWER(email) = LOWER(%s)
                            THEN 'email'
                    END
                FROM users
                WHERE LOWER(username) = LOWER(%s)
                   OR LOWER(email) = LOWER(%s)
                LIMIT 1;
                """,
                (username, email, username, email),
            ).fetchone()
        return row[0] if row else None

    def create_user(self, username: str, email: str, password_hash: str) -> dict:
        duplicate = self.find_existing_user(username, email)
        if duplicate:
            raise DuplicateError(duplicate)
        with _connect() as conn:
            row = conn.execute(
                """
                INSERT INTO users (username, email, password_hash)
                VALUES (%s, %s, %s)
                RETURNING id, created_at
                """,
                (username, email, password_hash),
            ).fetchone()
        return {
            "id": row[0],
            "username": username,
            "email": email,
            "password_hash": password_hash,
            "created_at": _iso(row[1]),
        }

    def get_user(self, user_id: int) -> dict | None:
        with _connect() as conn:
            row = conn.execute(
                "SELECT id, username, email FROM users WHERE id = %s",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return {"id": row[0], "username": row[1], "email": row[2]}

    def find_user_by_email(self, email: str) -> dict | None:
        email = email.strip().casefold()
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT id, username, email
                FROM users
                WHERE LOWER(email) = %s
                LIMIT 1
                """,
                (email,),
            ).fetchone()
        if not row:
            return None
        return {"id": row[0], "username": row[1], "email": row[2]}
    
    
    
    def set_trashed(self, file_id: int, trashed: bool) -> dict | None:
        with _connect() as conn:
            row = conn.execute(
                f"""
                UPDATE files
                SET trashed_at = CASE WHEN %s THEN NOW() ELSE NULL END,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING {_FILE_COLUMNS}
                """,
                (trashed, file_id),
            ).fetchone()

        return _file_row(row) if row else None


    def file_name_exists(self, owner_id: int, file_name: str) -> bool:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM files
                WHERE owner_id = %s AND LOWER(file_name) = LOWER(%s)
                LIMIT 1
                """,
                (owner_id, file_name),
            ).fetchone()
        return row is not None
    
     
    def create_file(self, owner_id, file_name, file_size, mime_type, chunks=None) -> dict:
        with _connect() as conn:
            duplicate = conn.execute(
                """
                SELECT 1
                FROM files
                WHERE owner_id = %s
                AND LOWER(file_name) = LOWER(%s)
                LIMIT 1
                """,
                (owner_id, file_name),
            ).fetchone()

            if duplicate:
                raise DuplicateError("file_name")

            row = conn.execute(
                """
                INSERT INTO files
                    (owner_id, file_name, file_size, mime_type, version)
                VALUES (%s, %s, %s, %s, 1)
                RETURNING
                    id, owner_id, file_name, file_size, mime_type,
                    created_at, updated_at, version, trashed_at
                """,
                (owner_id, file_name, file_size, mime_type),
            ).fetchone()

            for chunk in chunks or []:
                conn.execute(
                    """
                    INSERT INTO file_chunks
                        (file_id, chunk_index, chunk_hash,
                        chunk_size, storage_path, version)
                    VALUES (%s, %s, %s, %s, %s, 1)
                    """,
                    (
                        row[0],
                        chunk["chunk_index"],
                        chunk["chunk_hash"],
                        chunk["chunk_size"],
                        chunk["storage_path"],
                    ),
                )

        return _file_row(row)

    def starred_ids(self, user_id: int) -> set:
        return set()


    def list_edit_sessions(self, user_id=None, file_id=None, status="active") -> list:
        return []


    def list_files(self, owner_id: int) -> list[dict]:
        with _connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {_FILE_COLUMNS}
                FROM files
                WHERE owner_id = %s
                ORDER BY created_at DESC, id DESC
                """,
                (owner_id,),
            ).fetchall()
        return [_file_row(row) for row in rows]

    def get_file(self, file_id: int) -> dict | None:
        with _connect() as conn:
            row = conn.execute(
                f"SELECT {_FILE_COLUMNS} FROM files WHERE id = %s",
                (file_id,),
            ).fetchone()
        return _file_row(row) if row else None

    
    def get_file_chunks(self, file_id: int, version: int | None = None) -> list[dict]:
        with _connect() as conn:
            if version is None:
                file_row = conn.execute(
                    "SELECT version FROM files WHERE id = %s",
                    (file_id,),
                ).fetchone()

                if not file_row:
                    return []

                version = file_row[0]

            rows = conn.execute(
                """
                SELECT
                    id, file_id, chunk_index, chunk_hash,
                    chunk_size, storage_path, version
                FROM file_chunks
                WHERE file_id = %s AND version = %s
                ORDER BY chunk_index
                """,
                (file_id, version),
            ).fetchall()

        return [
            {
                "id": r[0],
                "file_id": r[1],
                "chunk_index": r[2],
                "chunk_hash": r[3].strip(),
                "chunk_size": r[4],
                "storage_path": r[5],
                "version": r[6],
            }
            for r in rows
        ]
    
    def list_shared_with(self, user_id: int) -> list[dict]:
        return []
    
    
    def replace_file_content(
        self, file_id: int, expected_version: int,
        file_size: int, chunks: list[dict]
    ) -> dict:
        with _connect() as conn:
            current = conn.execute(
                """
                SELECT version
                FROM files
                WHERE id = %s
                FOR UPDATE
                """,
                (file_id,),
            ).fetchone()

            if not current:
                raise FileNotFoundError(f"File {file_id} not found")

            current_version = current[0]

            if current_version != expected_version:
                raise VersionConflict(
                    f"Expected version {expected_version}, "
                    f"but current version is {current_version}"
                )

            new_version = current_version + 1

            for chunk in chunks:
                conn.execute(
                    """
                    INSERT INTO file_chunks
                        (file_id, chunk_index, chunk_hash,
                        chunk_size, storage_path, version)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        file_id,
                        chunk["chunk_index"],
                        chunk["chunk_hash"],
                        chunk["chunk_size"],
                        chunk["storage_path"],
                        new_version,
                    ),
                )

            row = conn.execute(
                """
                UPDATE files
                SET file_size = %s,
                    version = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING
                    id, owner_id, file_name, file_size, mime_type,
                    created_at, updated_at, version, trashed_at
                """,
                (file_size, new_version, file_id),
            ).fetchone()

        return _file_row(row)



    def log_activity(self, user_id, file_id, action, details=None) -> None:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO activity_logs (user_id, file_id, action, details)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (user_id, file_id, action, json.dumps(details) if details is not None else None),
            )

    # ------------------------------------------------------------------
    # Not yet implemented for PostgreSQL: JSON is the active development
    # backend. These fail loudly instead of silently misbehaving.
    # ------------------------------------------------------------------
    def _json_only(self, *args, **kwargs):
        raise NotImplementedError(
            "This operation is only implemented for the JSON backend "
            "(SYNAPSE_STORAGE=json) during development."
        )

    get_permission = _json_only
    set_permission = _json_only
    remove_permission = _json_only
    list_permissions = _json_only
    def list_shared_with(self, user_id: int) -> list[dict]:
        return []

    create_edit_session = _json_only
    get_edit_session = _json_only
    update_edit_session = _json_only

    set_star = _json_only
    
      
