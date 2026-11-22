"""Storage backend selection and shared paths.

    SYNAPSE_STORAGE      "json" (default) or "postgres"
    SYNAPSE_DATA_DIR     JSON tables + search index   (default: <repo>/data)
    SYNAPSE_STORAGE_DIR  content-addressed chunk store (default: <repo>/storage)
"""

import os
from functools import lru_cache
from pathlib import Path

from src.py.json_store import DuplicateError, JSONStore  # noqa: F401  (re-export)


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("SYNAPSE_DATA_DIR", BASE_DIR / "data"))
STORAGE_DIR = Path(os.getenv("SYNAPSE_STORAGE_DIR", BASE_DIR / "storage"))
TMP_DIR = DATA_DIR / "tmp"
LOCKS_DIR = DATA_DIR / "locks"   # written only by the C++ synapse_lock binary


@lru_cache(maxsize=1)
def get_store():
    backend = os.getenv("SYNAPSE_STORAGE", "json").strip().casefold()
    if backend == "json":
        return JSONStore(DATA_DIR)
    if backend == "postgres":
        from src.py.postgres_store import PostgresStore
        return PostgresStore()
    raise RuntimeError(
        f"Unknown SYNAPSE_STORAGE={backend!r}; use 'json' or 'postgres'."
    )
