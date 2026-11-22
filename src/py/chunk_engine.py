"""Python bridge to the C++ chunk engine (src/cpp, `synapse_engine`).

The engine itself is unchanged; this module only calls its CLI:

    synapse_engine split <source> <storage_dir> [chunk_size]   -> JSON manifest
    synapse_engine merge <dest>   <storage_dir> <h1,h2,...>    -> JSON status

On Windows the binary is a Linux ELF, so it is run through `wsl.exe -e` and
every path is translated to its /mnt/<drive>/... form.  On Linux (or when
the backend itself runs inside WSL) it is run directly.

    SYNAPSE_ENGINE        path to the binary
                          (default: <repo>/src/cpp/build/synapse_engine)
    SYNAPSE_ENGINE_MODE   "wsl" (default on Windows) or "native"
    SYNAPSE_CHUNK_SIZE    chunk size in bytes (default: engine default, 1 MiB)
    SYNAPSE_ENGINE_TIMEOUT seconds per engine call (default 300)
"""

import json
import os
import re
import subprocess
from pathlib import Path, PureWindowsPath

from src.py.store import BASE_DIR, STORAGE_DIR


ENGINE_PATH = Path(os.getenv(
    "SYNAPSE_ENGINE", BASE_DIR / "src" / "cpp" / "build" / "synapse_engine"
))
ENGINE_MODE = os.getenv(
    "SYNAPSE_ENGINE_MODE", "wsl" if os.name == "nt" else "native"
).strip().casefold()
CHUNK_SIZE = os.getenv("SYNAPSE_CHUNK_SIZE")
TIMEOUT = int(os.getenv("SYNAPSE_ENGINE_TIMEOUT", "300"))

# Windows limits a whole command line to 32,767 characters. Each SHA-256
# hash costs 65 characters in the merge argument, so leave headroom.
MAX_MERGE_ARG_CHARS = 30_000

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class ChunkEngineError(RuntimeError):
    pass


def to_wsl_path(path: Path) -> str:
    """C:\\a\\b -> /mnt/c/a/b (assumes WSL's default /mnt automount)."""
    win = PureWindowsPath(os.path.abspath(path))
    if not win.drive or not win.drive.endswith(":"):
        raise ChunkEngineError(f"Cannot translate path for WSL: {path}")
    drive = win.drive[0].lower()
    rest = "/".join(win.parts[1:])
    return f"/mnt/{drive}/{rest}"


def _arg_path(path: Path) -> str:
    return to_wsl_path(path) if ENGINE_MODE == "wsl" else str(Path(path).resolve())


def _run(args: list[str]) -> dict:
    engine = _arg_path(ENGINE_PATH)
    command = ["wsl.exe", "-e", engine, *args] if ENGINE_MODE == "wsl" else [engine, *args]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ChunkEngineError(
            "Could not start the chunk engine "
            f"({'wsl.exe' if ENGINE_MODE == 'wsl' else engine})."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ChunkEngineError("The chunk engine timed out.") from exc

    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")

    if result.returncode != 0:
        message = stderr.strip() or stdout.strip() or f"exit code {result.returncode}"
        try:
            message = json.loads(message).get("message", message)
        except (ValueError, AttributeError):
            pass
        raise ChunkEngineError(f"Chunk engine failed: {message}")

    try:
        data = json.loads(stdout)
    except ValueError as exc:
        raise ChunkEngineError(f"Chunk engine returned invalid JSON: {stdout[:200]!r}") from exc

    if data.get("status") != "success":
        raise ChunkEngineError(f"Chunk engine error: {data.get('message', data)}")
    return data


def chunk_storage_path(chunk_hash: str) -> str:
    """Path of a chunk relative to STORAGE_DIR, mirroring CASStorage::get_chunk_path."""
    return f"{chunk_hash[:2]}/{chunk_hash}"


def split_file(source: Path) -> dict:
    """Split `source` into STORAGE_DIR and return the engine's manifest.

    Manifest: {"total_size", "chunk_size", "total_chunks",
               "chunks": [{"index", "hash", "size"}, ...]}
    """
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    args = ["split", _arg_path(source), _arg_path(STORAGE_DIR)]
    if CHUNK_SIZE:
        args.append(str(int(CHUNK_SIZE)))

    manifest = _run(args)

    chunks = manifest.get("chunks", [])
    for expected_index, chunk in enumerate(chunks):
        if chunk.get("index") != expected_index or not _HASH_RE.match(str(chunk.get("hash", ""))):
            raise ChunkEngineError("Chunk engine returned a malformed manifest.")
    if sum(int(c["size"]) for c in chunks) != int(manifest.get("total_size", -1)):
        raise ChunkEngineError("Chunk sizes do not add up to the file size.")
    return manifest


def merge_chunks(chunk_hashes: list[str], dest: Path) -> None:
    """Reassemble the given chunks (in order) into `dest`."""
    if not chunk_hashes:
        raise ChunkEngineError("No chunks to merge.")
    for chunk_hash in chunk_hashes:
        if not _HASH_RE.match(chunk_hash):
            raise ChunkEngineError(f"Invalid chunk hash: {chunk_hash!r}")

    hashes_arg = ",".join(chunk_hashes)
    if ENGINE_MODE == "wsl" and len(hashes_arg) > MAX_MERGE_ARG_CHARS:
        raise ChunkEngineError(
            f"File has {len(chunk_hashes)} chunks, too many to pass to the engine on "
            "the Windows command line. Increase SYNAPSE_CHUNK_SIZE for large files."
        )

    _run(["merge", _arg_path(dest), _arg_path(STORAGE_DIR), hashes_arg])
