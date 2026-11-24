import mimetypes
import os
import secrets
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Literal
from urllib.parse import quote
from semantic_search.manage_index import add_file

from fastapi import (
    APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from src.py import chunk_engine, lock_client, office_templates
from src.py.store import TMP_DIR, DuplicateError, get_store

LOCKED_BY_OTHER = "This file is currently being used by another user."


router = APIRouter(prefix="/api/workspace", tags=["workspace"])

SESSION_COOKIE = "synapse_session"
SESSION_LIFETIME = 8 * 60 * 60
FOLDER_MIME_TYPE = "application/x-synapse-folder"
sessions: dict[str, tuple[int, float]] = {}
sessions_lock = Lock()


class WorkspaceItemRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["file", "folder"]


class ShareRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    permission: Literal["read", "write"] = "write"


def create_session(response: Response, user_id: int) -> None:
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + SESSION_LIFETIME
    with sessions_lock:
        sessions[token] = (user_id, expires_at)

    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_LIFETIME,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "false").casefold() == "true",
        samesite="lax",
        path="/",
    )


def get_current_user_id(request: Request) -> int:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Please log in.")

    with sessions_lock:
        session = sessions.get(token)
        if session and session[1] <= time.time():
            sessions.pop(token, None)
            session = None

    if not session:
        raise HTTPException(status_code=401, detail="Please log in.")

    return session[0]


def _item_response(row: dict, **extra):
    is_folder = row["mime_type"] == FOLDER_MIME_TYPE
    return {
        "id": str(row["id"]),
        "name": row["file_name"],
        "size": row["file_size"],
        "type": "Folder" if is_folder else row["mime_type"] or "File",
        "kind": "folder" if is_folder else "file",
        "extension": "" if is_folder else os.path.splitext(row["file_name"])[1].lstrip(".").lower(),
        "version": int(row.get("version", 1)),
        "modified": row.get("updated_at") or row["created_at"],
        **extra,
    }


def _clean_name(raw: str | None) -> str:
    # Browsers may send a path ("C:\fakepath\a.txt" or "dir/a.txt"); keep the base name.
    name = (raw or "").replace("\\", "/").split("/")[-1].strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name cannot be empty.")
    if len(name) > 255:
        raise HTTPException(status_code=422, detail="Name is too long (max 255 characters).")
    return name


def _duplicate_error() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail="An item with this name already exists.",
    )


def _save_item(
    user_id: int,
    name: str,
    size: int,
    mime_type: str | None,
    action: str,
    chunks: list[dict] | None = None,
) -> dict:
    store = get_store()
    try:
        row = store.create_file(user_id, name, size, mime_type, chunks)
    except DuplicateError as exc:
        raise _duplicate_error() from exc

    store.log_activity(user_id, row["id"], action, {"file_name": name})
    return row


def access_level(row: dict, user_id: int) -> str | None:
    """"owner", "write", "read" or None for a file row and user."""
    if row["owner_id"] == user_id:
        return "owner"
    return get_store().get_permission(row["id"], user_id)


def get_accessible_file(
    file_id: int, user_id: int, need: str = "read", allow_trashed: bool = False
) -> tuple[dict, str]:
    """Return (file row, access level) or raise.

    404 when the user has no access at all (so file ids of other users are
    not revealed) or the file is in Trash; 403 when they can see it but need
    write access. Trashed files are reachable only by their owner, and only
    where `allow_trashed` is set (restore).
    """
    row = get_store().get_file(file_id)
    access = access_level(row, user_id) if row else None
    if row and row.get("trashed_at") and not (allow_trashed and access == "owner"):
        access = None
    if not access:
        raise HTTPException(status_code=404, detail="File not found.")
    if need == "write" and access == "read":
        raise HTTPException(
            status_code=403,
            detail="You only have read access to this file.",
        )
    return row, access


def _iso(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def lock_view(lock: dict | None, user_id: int, names: dict[int, str] | None = None) -> dict:
    """Public description of a C++ lock record for the API/frontend."""
    if not lock:
        return {"locked": False}
    owner_id = lock.get("user_id")
    if names is not None and owner_id is not None and owner_id not in names:
        owner = get_store().get_user(owner_id)
        names[owner_id] = owner["username"] if owner else "unknown user"
    view = {
        "locked": True,
        "state": lock.get("state"),
        "owner_id": owner_id,
        "owner": (names or {}).get(owner_id) if owner_id is not None else None,
        "is_mine": owner_id == user_id,
        "acquired_at": _iso(lock.get("acquired_at")),
        "lease_expires": _iso(lock.get("lease_expires")),
        "last_heartbeat": _iso(lock.get("last_heartbeat")),
    }
    if owner_id == user_id:
        view["session_id"] = lock.get("session_id")
    return view


def _describe(row: dict, user_id: int, access: str, names: dict[int, str],
              lock: dict | None = None, starred: set[int] | None = None, **extra) -> dict:
    owner_id = row["owner_id"]
    if owner_id not in names:
        owner = get_store().get_user(owner_id)
        names[owner_id] = owner["username"] if owner else "unknown user"
    if starred is None:
        starred = get_store().starred_ids(user_id)
    return _item_response(
        row,
        owner=names[owner_id],
        owner_id=owner_id,
        access=access,
        lock=lock_view(lock, user_id, names),
        starred=row["id"] in starred,
        trashed_at=row.get("trashed_at"),
        **extra,
    )


VIEW_SCOPES = ("all", "mine", "shared", "starred", "recent", "trash")
RECENT_DAYS = 7


def _accessible_rows(user_id: int, scope: str = "all") -> list[tuple[dict, str]]:
    """(row, access) pairs for one dashboard view.

    all     my files + files shared with me (not in Trash)
    mine    my files          shared   files shared with me
    starred starred files     recent   modified in the last RECENT_DAYS days
    trash   my trashed files
    """
    store = get_store()
    own = store.list_files(user_id)
    if scope == "trash":
        return [(row, "owner") for row in own if row.get("trashed_at")]

    rows: list[tuple[dict, str]] = []
    if scope != "shared":
        rows += [(row, "owner") for row in own if not row.get("trashed_at")]
    if scope != "mine":
        rows += [
            (row, row.pop("permission")) for row in store.list_shared_with(user_id)
            if not row.get("trashed_at")
        ]
    if scope == "starred":
        starred = store.starred_ids(user_id)
        rows = [(row, access) for row, access in rows if row["id"] in starred]
    elif scope == "recent":
        cutoff = datetime.now(timezone.utc).timestamp() - RECENT_DAYS * 86400
        rows = [
            (row, access) for row, access in rows
            if datetime.fromisoformat(row.get("updated_at") or row["created_at"]).timestamp() >= cutoff
        ]
    return rows


def _all_locks() -> dict[int, dict] | None:
    """Every current lock, or None if the lock manager is unavailable."""
    try:
        return lock_client.list_locks()
    except lock_client.LockError:
        return None


def ensure_not_locked_by_other(file_id: int, user_id: int) -> dict | None:
    """Raise 409 if another user holds the lock. Fails closed: if the lock
    manager cannot be asked, access is denied (500) rather than allowed."""
    try:
        lock = lock_client.status(file_id)
    except lock_client.LockError as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not check the file lock: {exc}"
        ) from exc
    if lock and lock.get("user_id") != user_id:
        raise HTTPException(status_code=409, detail=LOCKED_BY_OTHER)
    return lock


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


@router.get("/me")
def current_user(user_id: int = Depends(get_current_user_id)):
    user = get_store().get_user(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Please log in.")
    return {"id": user["id"], "username": user["username"], "email": user["email"]}


@router.get("/files")
def list_workspace_items(
    scope: Literal["all", "mine", "shared", "starred", "recent", "trash"] = "all",
    user_id: int = Depends(get_current_user_id),
):
    """One dashboard view (see _accessible_rows), with owner, access, star
    and lock status for every item."""
    locks = _all_locks()
    names: dict[int, str] = {}
    starred = get_store().starred_ids(user_id)
    items = []
    for row, access in _accessible_rows(user_id, scope):
        item = _describe(row, user_id, access, names, (locks or {}).get(row["id"]), starred)
        if locks is None:
            item["lock"] = {"locked": None, "state": "unknown"}
        items.append(item)
    items.sort(key=lambda i: i["modified"], reverse=True)
    return items


@router.post("/files/{file_id}/star")
def star_file(file_id: int, user_id: int = Depends(get_current_user_id)):
    get_accessible_file(file_id, user_id)
    get_store().set_star(user_id, file_id, True)
    return {"id": str(file_id), "starred": True}


@router.delete("/files/{file_id}/star")
def unstar_file(file_id: int, user_id: int = Depends(get_current_user_id)):
    get_accessible_file(file_id, user_id, allow_trashed=True)
    get_store().set_star(user_id, file_id, False)
    return {"id": str(file_id), "starred": False}


@router.post("/files/{file_id}/trash")
def trash_file(file_id: int, user_id: int = Depends(get_current_user_id)):
    """Move a file to Trash (owner only). Refused while anyone is editing it.

    The C++ lock is taken for the duration of the change, so a file cannot
    be trashed while it is open, nor opened while it is being trashed.
    """
    row, access = get_accessible_file(file_id, user_id)
    if access != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can move this file to Trash.")

    session_id = "trash-" + secrets.token_urlsafe(16)
    try:
        reply = lock_client.acquire(file_id, user_id, session_id)
    except lock_client.LockError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not reply.ok:
        holder = (reply.lock or {}).get("user_id")
        detail = (
            "You have this file open for editing. Save to Synapse or discard your changes first."
            if holder == user_id else LOCKED_BY_OTHER
        )
        raise HTTPException(status_code=409, detail=detail)
    try:
        updated = get_store().set_trashed(file_id, True)
        get_store().log_activity(user_id, file_id, "file_trashed", {"file_name": row["file_name"]})
    finally:
        try:
            lock_client.release(file_id, user_id, session_id)
        except lock_client.LockError:
            pass  # recovered by the abandon timeout
    return _describe(updated, user_id, "owner", {}, None)


@router.post("/files/{file_id}/restore")
def restore_file(file_id: int, user_id: int = Depends(get_current_user_id)):
    row, access = get_accessible_file(file_id, user_id, allow_trashed=True)
    if access != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can restore this file.")
    if not row.get("trashed_at"):
        raise HTTPException(status_code=400, detail="This file is not in Trash.")
    updated = get_store().set_trashed(file_id, False)
    get_store().log_activity(user_id, file_id, "file_restored", {"file_name": row["file_name"]})
    return _describe(updated, user_id, "owner", {}, None)


@router.get("/files/{file_id}/permissions")
def list_file_permissions(file_id: int, user_id: int = Depends(get_current_user_id)):
    row, access = get_accessible_file(file_id, user_id)
    if access != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can view sharing.")
    store = get_store()
    result = []
    for perm in store.list_permissions(file_id):
        user = store.get_user(perm["user_id"])
        result.append({
            "username": user["username"] if user else "unknown user",
            "permission": perm["permission"],
        })
    return result


@router.post("/files/{file_id}/share")
def share_file(
    file_id: int,
    request: ShareRequest,
    user_id: int = Depends(get_current_user_id),
):
    row, access = get_accessible_file(file_id, user_id)
    if access != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can share this file.")
    if row["mime_type"] == FOLDER_MIME_TYPE:
        raise HTTPException(status_code=400, detail="Folders cannot be shared yet.")

    store = get_store()
    target = store.find_user_by_username(request.username)
    if not target:
        raise HTTPException(status_code=404, detail="No user with that username.")
    if target["id"] == user_id:
        raise HTTPException(status_code=400, detail="You already own this file.")

    store.set_permission(file_id, target["id"], request.permission)
    store.log_activity(user_id, file_id, "file_shared", {
        "with": target["username"], "permission": request.permission,
    })
    return {"username": target["username"], "permission": request.permission}


@router.delete("/files/{file_id}/share/{username}")
def unshare_file(
    file_id: int,
    username: str,
    user_id: int = Depends(get_current_user_id),
):
    row, access = get_accessible_file(file_id, user_id)
    if access != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can change sharing.")
    store = get_store()
    target = store.find_user_by_username(username)
    if not target or not store.remove_permission(file_id, target["id"]):
        raise HTTPException(status_code=404, detail="This file is not shared with that user.")
    store.log_activity(user_id, file_id, "file_unshared", {"with": target["username"]})
    return {"username": target["username"], "removed": True}


@router.post("/items")
def create_workspace_item(
    item: WorkspaceItemRequest,
    user_id: int = Depends(get_current_user_id),
):
    name = item.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name cannot be empty.")

    is_folder = item.kind == "folder"
    if is_folder:
        row = _save_item(user_id, name, 0, FOLDER_MIME_TYPE, "folder_created")
        return _describe(row, user_id, "owner", {}, None)

    if "." not in name:
        if len(name) > 251:
            raise HTTPException(
                status_code=422,
                detail="File name is too long after adding the .txt extension.",
            )
        name += ".txt"

    extension = os.path.splitext(name)[1].casefold()
    if extension not in office_templates.CREATABLE:
        allowed = ", ".join(sorted(office_templates.CREATABLE))
        raise HTTPException(
            status_code=400,
            detail=f"Create File supports {allowed}. Upload other file types instead.",
        )
    mime_type = office_templates.CREATABLE[extension]
    content = office_templates.blank_document(extension)

    if not content:  # plain-text formats start empty: no chunks needed
        row = _save_item(user_id, name, 0, mime_type, "file_created")
        return _describe(row, user_id, "owner", {}, None)

    # Office formats: store a valid blank document through the chunk engine.
    if get_store().file_name_exists(user_id, name):
        raise _duplicate_error()
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(TMP_DIR), prefix="create_")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
        try:
            manifest = chunk_engine.split_file(tmp_path)
        except chunk_engine.ChunkEngineError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        chunks = [
            {
                "chunk_index": c["index"],
                "chunk_hash": c["hash"],
                "chunk_size": c["size"],
                "storage_path": chunk_engine.chunk_storage_path(c["hash"]),
            }
            for c in manifest["chunks"]
        ]
        row = _save_item(user_id, name, len(content), mime_type, "file_created", chunks)
    finally:
        _remove_quietly(tmp_path)
    return _describe(row, user_id, "owner", {}, None)


@router.post("/upload")
def upload_file(
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
):
    """Store the file's bytes via the C++ chunk engine and record its chunks."""
    name = _clean_name(file.filename)
    store = get_store()
    if store.file_name_exists(user_id, name):  # fail fast before chunking
        raise _duplicate_error()

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(TMP_DIR), prefix="upload_")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(file.file, out, length=1024 * 1024)
        size = tmp_path.stat().st_size

        try:
            manifest = chunk_engine.split_file(tmp_path)
        except chunk_engine.ChunkEngineError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        if int(manifest["total_size"]) != size:
            raise HTTPException(
                status_code=500,
                detail="Chunk engine size does not match the uploaded file.",
            )

        chunks = [
            {
                "chunk_index": c["index"],
                "chunk_hash": c["hash"],
                "chunk_size": c["size"],
                "storage_path": chunk_engine.chunk_storage_path(c["hash"]),
            }
            for c in manifest["chunks"]
        ]
        # If this loses a race with a same-name upload, its chunks stay in the
        # content-addressed store and are reused by any later identical upload.
        row = _save_item(
            user_id, name, size, file.content_type or None, "file_uploaded", chunks
        )
        try:
            add_file(row["id"], tmp_path)
        except Exception as exc:
            print(f"Semantic indexing failed for file {row['id']}: {exc}")
        return _item_response(row, chunks=len(chunks))
    finally:
        file.file.close()
        _remove_quietly(tmp_path)


@router.get("/files/{file_id}/download")
def download_file(
    file_id: int,
    user_id: int = Depends(get_current_user_id),
):
    """Reassemble a file from its chunks with the C++ engine and send it.

    Enforced on the server: read access is required, and while another user
    holds the exclusive lock the download is refused (409), so the lock
    cannot be bypassed by calling this endpoint directly.
    """
    row, _access = get_accessible_file(file_id, user_id)
    if row["mime_type"] == FOLDER_MIME_TYPE:
        raise HTTPException(status_code=400, detail="Folders cannot be downloaded.")
    ensure_not_locked_by_other(file_id, user_id)

    response = send_file_version(row, int(row.get("version", 1)), prefix="download_")
    get_store().log_activity(
        user_id, file_id, "file_downloaded", {"file_name": row["file_name"]}
    )
    return response


# Office formats are not always in the OS MIME registry (notably on Windows).
_EXTRA_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".ppt": "application/vnd.ms-powerpoint",
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
}


def media_type_for(row: dict) -> str:
    """The stored MIME type, or one guessed from the extension if the browser
    sent nothing useful. The bytes are always sent unchanged."""
    stored = row.get("mime_type")
    if stored and stored not in ("application/octet-stream", FOLDER_MIME_TYPE):
        return stored
    ext = os.path.splitext(row["file_name"])[1].casefold()
    return _EXTRA_TYPES.get(ext) or mimetypes.guess_type(row["file_name"])[0] or "application/octet-stream"


def send_file_version(row: dict, version: int, prefix: str) -> Response:
    """Rebuild one version of a file from its chunks (C++ merge) into a
    temporary file under data/tmp and stream it as an attachment in its
    original format. The temporary file is deleted after sending; the
    stored chunks are never modified."""
    media_type = media_type_for(row)
    headers = {"X-Content-Type-Options": "nosniff"}
    chunks = get_store().get_file_chunks(row["id"], version)

    if not chunks:
        if row["file_size"] != 0:
            raise HTTPException(
                status_code=409,
                detail="This file was saved as metadata only; its contents are not on the server.",
            )
        headers["Content-Disposition"] = (
            "attachment; filename*=utf-8''" + quote(row["file_name"])
        )
        return Response(b"", media_type=media_type, headers=headers)

    expected_size = sum(int(c["chunk_size"]) for c in chunks)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(TMP_DIR), prefix=prefix)
    os.close(fd)
    out_path = Path(tmp_name)
    try:
        chunk_engine.merge_chunks([c["chunk_hash"] for c in chunks], out_path)
        if out_path.stat().st_size != expected_size:
            raise chunk_engine.ChunkEngineError("Reassembled size does not match.")
    except (chunk_engine.ChunkEngineError, OSError) as exc:
        _remove_quietly(out_path)
        raise HTTPException(status_code=500, detail=f"Could not rebuild file: {exc}") from exc

    return FileResponse(
        out_path,
        filename=row["file_name"],
        media_type=media_type,
        headers=headers,
        background=BackgroundTask(_remove_quietly, out_path),
    )


def _keyword_rank(row: dict, owner_name: str, terms: list[str], phrase: str) -> int | None:
    """Rank a file for a keyword query; None means "no match".

    Every term must match at least one field (name, extension, type, owner).
    Lower rank = better: 0 exact name, 1 name starts with query,
    2 query inside name, 3 matched only through other metadata.
    """
    name = row["file_name"].casefold()
    stem, ext = os.path.splitext(name)
    is_folder = row["mime_type"] == FOLDER_MIME_TYPE
    fields = [
        name,
        ext.lstrip("."),
        "folder" if is_folder else (row["mime_type"] or "").casefold(),
        owner_name.casefold(),
    ]
    if not all(any(term in field for field in fields) for term in terms):
        return None
    if phrase in (name, stem):
        return 0
    if name.startswith(phrase):
        return 1
    if phrase in name:
        return 2
    return 3


@router.get("/search")
def keyword_search(
    q: str = Query(min_length=1, max_length=200),
    user_id: int = Depends(get_current_user_id),
):
    """Traditional keyword search over file metadata (no content, no AI).

    Partial and complete filename matching, plus extension / file type /
    owner matching.  Semantic search is a separate teammate module; see
    README "Search integration point".
    """
    phrase = " ".join(q.split()).casefold()
    terms = phrase.split()
    if not terms:
        raise HTTPException(status_code=400, detail="Search query is empty.")

    store = get_store()
    names: dict[int, str] = {}
    ranked = []
    for row, access in _accessible_rows(user_id):  # own + shared files
        owner_id = row["owner_id"]
        if owner_id not in names:
            owner = store.get_user(owner_id)
            names[owner_id] = owner["username"] if owner else ""
        rank = _keyword_rank(row, names[owner_id], terms, phrase)
        if rank is not None:
            ranked.append((rank, row["file_name"].casefold(), row, access))

    ranked.sort(key=lambda r: (r[0], r[1]))
    locks = _all_locks() or {}
    starred = store.starred_ids(user_id)
    return [
        _describe(row, user_id, access, names, locks.get(row["id"]), starred, match_rank=rank)
        for rank, _, row, access in ranked
    ]


@router.delete("/session", status_code=204)
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with sessions_lock:
            sessions.pop(token, None)
    response.delete_cookie(
        key=SESSION_COOKIE,
        httponly=True,
        secure=os.getenv("COOKIE_SECURE", "false").casefold() == "true",
        samesite="lax",
        path="/",
    )
