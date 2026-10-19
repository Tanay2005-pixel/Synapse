import os
import json
import secrets
import time
from threading import Lock
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.py.db_connection import get_db_connection


router = APIRouter(prefix="/api/workspace", tags=["workspace"])

SESSION_COOKIE = "synapse_session"
SESSION_LIFETIME = 8 * 60 * 60
FOLDER_MIME_TYPE = "application/x-synapse-folder"
sessions: dict[str, tuple[int, float]] = {}
sessions_lock = Lock()


class WorkspaceItemRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["file", "folder"]


class UploadMetadataRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0)
    mime_type: str | None = Field(default=None, max_length=255)


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


def _item_response(row):
    item_id, name, size, mime_type, created_at = row
    is_folder = mime_type == FOLDER_MIME_TYPE
    return {
        "id": str(item_id),
        "name": name,
        "size": size,
        "type": "Folder" if is_folder else mime_type or "File",
        "kind": "folder" if is_folder else "file",
        "modified": created_at.isoformat(),
    }


def _save_item(
    user_id: int,
    name: str,
    size: int,
    mime_type: str | None,
    action: str,
):
    with get_db_connection() as conn:
        duplicate = conn.execute(
            """
            SELECT 1
            FROM files
            WHERE owner_id = %s AND LOWER(file_name) = LOWER(%s)
            LIMIT 1
            """,
            (user_id, name),
        ).fetchone()
        if duplicate:
            raise HTTPException(
                status_code=409,
                detail="An item with this name already exists.",
            )

        row = conn.execute(
            """
            INSERT INTO files
                (owner_id, file_name, file_size, mime_type)
            VALUES (%s, %s, %s, %s)
            RETURNING id, file_name, file_size, mime_type, created_at
            """,
            (user_id, name, size, mime_type),
        ).fetchone()

        conn.execute(
            """
            INSERT INTO activity_logs (user_id, file_id, action, details)
            VALUES (%s, %s, %s, %s::jsonb)
            """,
            (
                user_id,
                row[0],
                action,
                json.dumps({"file_name": name}),
            ),
        )

    return _item_response(row)


@router.get("/files")
def list_workspace_items(user_id: int = Depends(get_current_user_id)):
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, file_name, file_size, mime_type, created_at
            FROM files
            WHERE owner_id = %s
            ORDER BY created_at DESC, id DESC
            """,
            (user_id,),
        ).fetchall()

    return [_item_response(row) for row in rows]


@router.post("/items")
def create_workspace_item(
    item: WorkspaceItemRequest,
    user_id: int = Depends(get_current_user_id),
):
    name = item.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name cannot be empty.")

    is_folder = item.kind == "folder"
    if not is_folder and "." not in name:
        if len(name) > 251:
            raise HTTPException(
                status_code=422,
                detail="File name is too long after adding the .txt extension.",
            )
        name += ".txt"

    mime_type = FOLDER_MIME_TYPE if is_folder else "text/plain"
    action = "folder_created" if is_folder else "file_created"
    return _save_item(user_id, name, 0, mime_type, action)


@router.post("/uploads")
def save_upload_metadata(
    item: UploadMetadataRequest,
    user_id: int = Depends(get_current_user_id),
):
    name = item.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name cannot be empty.")

    return _save_item(
        user_id,
        name,
        item.size,
        item.mime_type,
        "file_uploaded",
    )


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
