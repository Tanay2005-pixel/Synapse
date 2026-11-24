"""Desktop editing sessions: open a file with an exclusive lock, keep the
lock alive with heartbeats, and release it.

The exclusive lock is owned by the C++ lock manager (src/py/lock_client.py
-> synapse_lock). This module never decides lock ownership itself; it only
keeps session metadata in data/edit_sessions.json and maps the lock
manager's answers to HTTP responses:

    200  success                 401  not logged in
    400  invalid request         403  no write access / not your session
    404  file or session unknown 409  locked by another user, session no
                                      longer active, or unsaved changes
    500  lock manager unavailable (fails closed: access is denied)

Session statuses:
    active     user holds the lock and is (or may be) editing on their computer
    released   user released the lock without saving to Synapse
    synced     working copy saved to Synapse, lock released       (Phase 5)
    discarded  user discarded their changes, lock released        (Phase 5)
    lost       the lock was reclaimed after the abandon timeout, or vanished
"""

import os
import secrets
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.py import chunk_engine, lock_client
from src.py.json_store import VersionConflict, utc_now
from src.py.store import TMP_DIR, get_store
from src.py.workspace import (
    FOLDER_MIME_TYPE,
    LOCKED_BY_OTHER,
    _clean_name,
    _describe,
    _remove_quietly,
    get_accessible_file,
    get_current_user_id,
    lock_view,
    send_file_version,
)
from semantic_search.manage_index import add_file

router = APIRouter(prefix="/api/workspace", tags=["editing"])

UNSAVED_WARNING = (
    "You have not saved your latest changes to Synapse. Saving inside Word, "
    "Excel or another desktop app only changes the copy on your computer; use "
    "Save to Synapse to upload it. Release anyway and keep the server file unchanged?"
)
LOST_MESSAGE = (
    "Your lock on this file is no longer valid (it was released or reclaimed "
    "after a long period without activity). The server file was not changed. "
    "Open the file again to continue."
)


DISCARD_WARNING = (
    "Discard your changes? The file on the Synapse server will stay exactly as "
    "it was, and the lock will be released so others can open it. Any edits in "
    "your local working copy will not be uploaded."
)


class ReleaseRequest(BaseModel):
    # Must be true to release an editing session that was not saved to Synapse.
    confirm_unsaved: bool = False


class DiscardRequest(BaseModel):
    confirm: bool = False


def _lock_call(func, *args) -> lock_client.LockReply:
    try:
        return func(*args)
    except lock_client.LockError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _mark(session_id: str, status: str) -> None:
    get_store().update_edit_session(session_id, status=status, closed_at=utc_now())


def _session_response(session: dict, lock: dict | None, user_id: int, **extra) -> dict:
    store = get_store()
    row = store.get_file(session["file_id"])
    names: dict[int, str] = {}
    return {
        "session_id": session["session_id"],
        "file_id": str(session["file_id"]),
        "status": session["status"],
        "base_version": session["base_version"],
        "created_at": session["created_at"],
        "last_heartbeat": session["last_heartbeat"],
        "working_copy_downloaded_at": session.get("working_copy_downloaded_at"),
        "working_copy_url": f"/api/workspace/sessions/{session['session_id']}/working-copy",
        "save_url": f"/api/workspace/sessions/{session['session_id']}/save",
        "lock": lock_view(lock, user_id, names),
        "file": _describe(row, user_id, "owner" if row and row["owner_id"] == user_id
                          else store.get_permission(row["id"], user_id) or "none",
                          names, lock) if row else None,
        **extra,
    }


def get_own_active_session(session_id: str, user_id: int) -> dict:
    """The caller's active session, or raise 404/403/409."""
    session = get_store().get_edit_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Editing session not found.")
    if session["user_id"] != user_id:
        raise HTTPException(
            status_code=403,
            detail="Only the user who opened this file can use or release its lock.",
        )
    if session["status"] != "active":
        raise HTTPException(
            status_code=409,
            detail=f"This editing session is no longer active ({session['status']}).",
        )
    return session


@router.post("/files/{file_id}/open")
def open_file(file_id: int, user_id: int = Depends(get_current_user_id)):
    """Acquire the exclusive lock and start (or resume) an editing session."""
    row, _access = get_accessible_file(file_id, user_id, need="write")
    if row["mime_type"] == FOLDER_MIME_TYPE:
        raise HTTPException(status_code=400, detail="Folders cannot be opened for editing.")

    store = get_store()

    # Resume this user's existing session if its lock is still theirs.
    for session in store.list_edit_sessions(user_id=user_id, file_id=file_id):
        reply = _lock_call(lock_client.acquire, file_id, user_id, session["session_id"])
        if reply.ok:
            session = store.update_edit_session(session["session_id"], last_heartbeat=utc_now())
            return _session_response(session, reply.lock, user_id, resumed=True)
        _mark(session["session_id"], "lost")

    session_id = secrets.token_urlsafe(24)
    reply = _lock_call(lock_client.acquire, file_id, user_id, session_id)

    if reply.code == lock_client.HELD:
        holder = reply.lock or {}
        if holder.get("user_id") == user_id:
            detail = "You already have this file open in another editing session."
        else:
            detail = LOCKED_BY_OTHER
        return JSONResponse(status_code=409, content={
            "detail": detail,
            "lock": lock_view(holder, user_id, {}),
        })
    if not reply.ok:
        raise HTTPException(status_code=500, detail=f"Unexpected lock reply: {reply.status}")

    # The file may have been moved to Trash just before we got the lock.
    current = store.get_file(file_id)
    if not current or current.get("trashed_at"):
        try:
            lock_client.release(file_id, user_id, session_id)
        except lock_client.LockError:
            pass
        raise HTTPException(status_code=404, detail="File not found.")
    row = current

    try:
        session = store.create_edit_session(
            session_id, file_id, user_id, int(row.get("version", 1))
        )
    except Exception as exc:
        # Never leave a lock without a session the user can see and release.
        try:
            lock_client.release(file_id, user_id, session_id)
        except lock_client.LockError:
            pass  # the abandon timeout will recover it
        raise HTTPException(status_code=500, detail="Could not record the editing session.") from exc

    extra = {"resumed": False}
    if reply.status == "reclaimed" and reply.previous:
        _mark(reply.previous["session_id"], "lost")
        extra["reclaimed_from"] = lock_view(reply.previous, user_id, {})
        store.log_activity(user_id, file_id, "lock_reclaimed", {
            "previous_user_id": reply.previous.get("user_id"),
        })
    store.log_activity(user_id, file_id, "lock_acquired", {"session_id": session_id})
    return _session_response(session, reply.lock, user_id, **extra)


@router.get("/files/{file_id}/lock")
def file_lock_status(file_id: int, user_id: int = Depends(get_current_user_id)):
    get_accessible_file(file_id, user_id)
    try:
        lock = lock_client.status(file_id)
    except lock_client.LockError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return lock_view(lock, user_id, {})


@router.get("/sessions")
def list_my_sessions(user_id: int = Depends(get_current_user_id)):
    """The caller's active editing sessions (working copies on their computer)."""
    try:
        locks = lock_client.list_locks()
    except lock_client.LockError:
        locks = {}
    return [
        _session_response(s, locks.get(s["file_id"]), user_id)
        for s in get_store().list_edit_sessions(user_id=user_id)
    ]


def _heartbeat(session: dict, user_id: int) -> dict:
    reply = _lock_call(lock_client.heartbeat, session["file_id"], user_id, session["session_id"])
    if reply.ok:
        get_store().update_edit_session(session["session_id"], last_heartbeat=utc_now())
        return {"session_id": session["session_id"], "file_id": str(session["file_id"]),
                "ok": True, "lock": lock_view(reply.lock, user_id, {})}
    _mark(session["session_id"], "lost")
    return {"session_id": session["session_id"], "file_id": str(session["file_id"]),
            "ok": False, "detail": LOST_MESSAGE}


@router.post("/sessions/heartbeat")
def heartbeat_all(user_id: int = Depends(get_current_user_id)):
    """Called periodically by any open Synapse tab to keep the user's locks."""
    return [_heartbeat(s, user_id) for s in get_store().list_edit_sessions(user_id=user_id)]


@router.post("/sessions/{session_id}/heartbeat")
def heartbeat_one(session_id: str, user_id: int = Depends(get_current_user_id)):
    result = _heartbeat(get_own_active_session(session_id, user_id), user_id)
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=LOST_MESSAGE)
    return result


def _require_lock(session: dict, user_id: int) -> lock_client.LockReply:
    """Ask the C++ lock manager whether this session still owns the lock.

    Uses a heartbeat, so a successful check also refreshes the lease and
    restarts the abandon timer: the lock cannot be reclaimed by anyone for
    at least the abandon period after this call returns.
    """
    reply = _lock_call(lock_client.heartbeat, session["file_id"], user_id, session["session_id"])
    if not reply.ok:
        _mark(session["session_id"], "lost")
        raise HTTPException(status_code=409, detail=LOST_MESSAGE)
    get_store().update_edit_session(session["session_id"], last_heartbeat=utc_now())
    return reply


def _end_session(session: dict, user_id: int, status: str, saved: bool) -> dict:
    """Release the C++ lock for this session and record how it ended."""
    session_id = session["session_id"]
    reply = _lock_call(lock_client.release, session["file_id"], user_id, session_id)
    if reply.code in (lock_client.NOT_OWNER, lock_client.NOT_LOCKED):
        _mark(session_id, "lost")
        raise HTTPException(status_code=409, detail=LOST_MESSAGE)
    if not reply.ok:
        raise HTTPException(status_code=500, detail=f"Unexpected lock reply: {reply.status}")

    _mark(session_id, status)
    get_store().log_activity(user_id, session["file_id"], f"session_{status}", {
        "session_id": session_id, "saved": saved,
    })
    return {"session_id": session_id, "status": status, "file_id": str(session["file_id"])}


@router.post("/sessions/{session_id}/release")
def release_session(
    session_id: str,
    request: ReleaseRequest | None = None,
    user_id: int = Depends(get_current_user_id),
):
    """Release the lock. If the session has not been saved to Synapse this
    requires explicit confirmation, because the user may have edits in their
    desktop app that would otherwise be silently left behind."""
    session = get_own_active_session(session_id, user_id)
    already_saved = session.get("synced_version") is not None
    if not already_saved and not (request and request.confirm_unsaved):
        raise HTTPException(status_code=409, detail=UNSAVED_WARNING)
    return _end_session(session, user_id, "synced" if already_saved else "released", already_saved)


@router.post("/sessions/{session_id}/discard")
def discard_session(
    session_id: str,
    request: DiscardRequest | None = None,
    user_id: int = Depends(get_current_user_id),
):
    """Discard Changes: leave the server file untouched and release the lock."""
    session = get_own_active_session(session_id, user_id)
    if not (request and request.confirm):
        raise HTTPException(status_code=409, detail=DISCARD_WARNING)
    return _end_session(session, user_id, "discarded", saved=False)


@router.get("/sessions/{session_id}/working-copy")
def download_working_copy(session_id: str, user_id: int = Depends(get_current_user_id)):
    """Download the file as it was when the session was opened, in its
    original format, so it can be edited in a desktop application. The
    server copy is not changed by downloading."""
    session = get_own_active_session(session_id, user_id)
    row, _access = get_accessible_file(session["file_id"], user_id, need="write")
    _require_lock(session, user_id)

    response = send_file_version(row, int(session["base_version"]), prefix="working_")
    store = get_store()
    store.update_edit_session(
        session_id,
        working_copy_downloaded_at=utc_now(),
        working_copy_downloads=int(session.get("working_copy_downloads", 0)) + 1,
    )
    store.log_activity(user_id, row["id"], "working_copy_downloaded", {"session_id": session_id})
    return response


@router.post("/sessions/{session_id}/save")
def save_to_synapse(
    session_id: str,
    file_id: int = Form(...),
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
):
    """Save to Synapse: replace the server file with the user's edited
    working copy, then release the lock.

    The original is never overwritten in place. The new bytes are chunked
    into new content-addressed chunks, and only then does one atomic JSON
    write switch the file to the new version. Any failure before that
    write leaves the server file exactly as it was, and the session and
    lock stay active so the user can retry.
    """
    store = get_store()
    try:
        session = get_own_active_session(session_id, user_id)
        if file_id != session["file_id"]:
            raise HTTPException(
                status_code=400,
                detail="This editing session belongs to a different file.",
            )
        row, _access = get_accessible_file(session["file_id"], user_id, need="write")

        uploaded_name = _clean_name(file.filename)
        original_ext = os.path.splitext(row["file_name"])[1].casefold()
        uploaded_ext = os.path.splitext(uploaded_name)[1].casefold()
        if uploaded_ext != original_ext:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"\"{uploaded_name}\" is not the same type of file as "
                    f"\"{row['file_name']}\". Choose your edited copy of "
                    f"{row['file_name']}. The server file was not changed."
                ),
            )

        _require_lock(session, user_id)

        TMP_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(TMP_DIR), prefix="save_")
        tmp_path = Path(tmp_name)
    except BaseException:
        file.file.close()
        raise

    try:
        with os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(file.file, out, length=1024 * 1024)
        size = tmp_path.stat().st_size
        if size == 0 and row["file_size"] > 0:
            raise HTTPException(
                status_code=400,
                detail="The selected file is empty. The server file was not changed.",
            )

        try:
            manifest = chunk_engine.split_file(tmp_path)
        except chunk_engine.ChunkEngineError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Upload failed; the server file was not changed. ({exc})",
            ) from exc
        if int(manifest["total_size"]) != size:
            raise HTTPException(
                status_code=500,
                detail="Upload check failed; the server file was not changed.",
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
        base_version = int(session["base_version"])
        current = [c["chunk_hash"] for c in store.get_file_chunks(row["id"], base_version)]
        changed = [c["chunk_hash"] for c in chunks] != current

        # Re-check ownership immediately before the commit (this heartbeat
        # also restarts the abandon timer, so nobody can reclaim it now).
        _require_lock(session, user_id)

        if changed:
            try:
                updated = store.replace_file_content(row["id"], base_version, size, chunks)
            except VersionConflict as exc:
                raise HTTPException(
                    status_code=409,
                    detail="The server file changed since you opened it, so your copy was not saved.",
                ) from exc
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail="Could not save; the server file was not changed.",
                ) from exc
        else:
            updated = row
        if changed:
            try:
                add_file(row["id"], tmp_path)
            except Exception as exc:
                print(f"Semantic re-indexing failed for file {row['id']}: {exc}")

        store.update_edit_session(
            session_id,
            synced_version=int(updated.get("version", 1)),
            synced_at=utc_now(),
            changed=changed,
        )
        store.log_activity(user_id, row["id"], "file_synced", {
            "session_id": session_id,
            "from_version": base_version,
            "to_version": int(updated.get("version", 1)),
            "changed": changed,
            "size": size,
            "uploaded_name": uploaded_name,
        })
    finally:
        file.file.close()
        _remove_quietly(tmp_path)

    # The content is saved; now release the lock.
    lock_released = True
    try:
        _end_session(store.get_edit_session(session_id), user_id, "synced", saved=True)
    except HTTPException:
        lock_released = False

    names: dict[int, str] = {}
    access = "owner" if updated["owner_id"] == user_id else "write"
    if not changed:
        message = "No changes were found, so the server file is unchanged. The lock was released."
    else:
        message = f"Saved to Synapse as version {updated.get('version', 1)}. The lock was released."
    if not lock_released:
        message = message.replace(
            "The lock was released.",
            "The lock could not be released automatically; use Release Lock.",
        )
    return {
        "status": "synced",
        "changed": changed,
        "lock_released": lock_released,
        "name_matches": uploaded_name.casefold() == row["file_name"].casefold(),
        "message": message,
        "file": _describe(store.get_file(row["id"]), user_id, access, names, None),
    }
