from fastapi import APIRouter, Depends, Query

from src.py.workspace import get_current_user_id
from semantic_search.manage_index import search_files

router = APIRouter(
    prefix="/api/semantic",
    tags=["Semantic Search"]
)

@router.get("/search")
def semantic_search(
    q: str = Query(min_length=1, max_length=200),
    user_id: int = Depends(get_current_user_id),
):
    from src.py.workspace import (
        _accessible_rows,
        _describe,
    )

    accessible = {
        row["id"]: (row, access)
        for row, access in _accessible_rows(user_id)
    }

    file_ids = list(accessible.keys())
    results = search_files(q, file_ids)
    response = []

    for result in results:
        file_id = result["file_id"]

        if file_id not in accessible:
            continue

        row, access = accessible[file_id]

        item = _describe(
            row,
            user_id,
            access,
            {},
            score=result["score"],
            snippet=result["snippet"],
        )

        response.append(item)

    return response