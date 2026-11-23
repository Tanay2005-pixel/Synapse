# Synapse – Multi-User File Management System

A LAN-Based Shared File Storage and Management Platform

## 1. Project Overview

**Synapse** is a multi-user file management system designed to allow multiple users connected to the same Local Area Network (LAN) to store, access, download, and manage files through a centralized server.

The project focuses on reliable file access, controlled permissions, efficient storage management, and safe handling of simultaneous file operations. It combines concepts from Operating Systems (OS), Database Management Systems (DBMS), and Artificial Intelligence/Machine Learning (AI/ML).

The system uses a web-based interface for user interaction, a FastAPI backend for request handling, PostgreSQL for storing user and file metadata, and C++ for implementing operating-system-related file management operations. The planned semantic search module uses embeddings and pgvector to retrieve files based on the meaning of a search query.

## 2. Problem Statement

When multiple users access a shared storage system simultaneously, problems such as conflicting file modifications, inefficient memory usage, slow file retrieval, and unauthorized access may occur.

Synapse aims to address these challenges through centralized file management, user authentication, access permissions, chunk-based file handling, concurrency control, and intelligent file search.

## 3. Project Objectives

- Develop a centralized file management platform accessible to multiple users over a LAN.
- Provide secure user authentication and controlled file access.
- Support file upload, download, organization, and sharing.
- Use chunk-based file handling to process files efficiently.
- Apply multithreading, synchronization, and file locking to manage concurrent operations.
- Store file metadata, permissions, versions, and activity records in a database.
- Develop semantic search using embeddings and vector similarity.
- Evaluate performance under multiple users and concurrent file operations.

## 4. Team Members

| Team Member | Role |
|---|---|
| Tanay Bansal | Frontend and Backend Development |
| Retesh Joshi | Backend Development and OS Concepts |
| Samya Kharbanda | AI/ML and API Integration |
| Ayush Joshi | Database Management and Vector Search |

**Team ID:** OSDBMS-V-T015  
**Team Name:** Lattice  
**Mentor:** Dr. Sumit Pundir  
**Institution:** Graphic Era (Deemed to be University), Dehradun

## 5. Technologies and Tools Used

| Technology | Purpose |
|---|---|
| HTML, CSS, JavaScript | Frontend interface |
| Python | Backend and AI/ML functionality |
| FastAPI | Backend API and request handling |
| C++ | File operations and OS-related modules |
| PostgreSQL | User accounts, file metadata, permissions, and activity records |
| pgvector | Vector storage and similarity search |
| Sentence Transformers | Text embeddings for semantic search |
| scikit-learn | Supporting machine learning functionality |
| Git and GitHub | Version control and team collaboration |
| Visual Studio Code | Development environment |

## 6. Major Features and Modules

### 6.1 User and Access Management
- User registration and login.
- Authentication and authorization.
- File ownership and access permissions.
- Controlled file sharing.

### 6.2 File Management and Storage
- Uploading and downloading files.
- Centralized storage on the server.
- File metadata management.
- Chunk-based file processing.
- File organization and retrieval.

### 6.3 Concurrent and Resource Management
- Multithreading for handling simultaneous requests.
- File locking and synchronization.
- Buffer management.
- Disk scheduling and efficient file I/O.
- Prevention of conflicting modifications.

### 6.4 Database Management
- User and file metadata storage.
- Permission management.
- File version information.
- Activity logging.
- Indexing for efficient retrieval.

### 6.5 AI/ML and Semantic Search
- Text embeddings for file content or metadata.
- Vector similarity search using pgvector.
- Retrieval of relevant files based on the meaning of a query.
- Potential future support for file categorization and access prediction.

### 6.6 Administration and Monitoring
- Storage usage monitoring.
- User and file management.
- Monitoring file requests and system activity.

*Note: Features listed here describe the planned system capabilities; their implementation status is provided separately below.*

## 7. Project Setup and Installation

### Prerequisites

Install the following software before running the project:

- Python 3.10 or a compatible version supported by the project dependencies.
- PostgreSQL with pgvector installed and enabled.
- A C++ compiler supporting the C++ standard used by the project.
- Git.
- Visual Studio Code or another suitable IDE.

### Step 1: Clone the Repository

```bash
git clone https://github.com/Tanay2005-pixel/Synapse.git
cd Synapse
```

### Step 2: Create and Activate a Virtual Environment

```bash
python -m venv venv
```

On Windows:

```bash
venv\Scripts\activate
```

### Step 3: Install Python Dependencies

If the repository contains a `requirements.txt` file, run:

```bash
pip install -r requirements.txt
```

If it does not, install the dependencies specified by the project configuration before starting the application.

### Development mode: JSON storage (default)

For how the backend works (which endpoint and function handles each action: upload, download, open, Save to Synapse and so on), see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). For the test report, see [docs/TESTING.md](docs/TESTING.md).

During development Synapse stores its data in JSON files instead of PostgreSQL. No database is needed.

| Setting (env var) | Default | Meaning |
|---|---|---|
| `SYNAPSE_STORAGE` | `json` | `json` or `postgres` |
| `SYNAPSE_DATA_DIR` | `data/` | JSON tables (`users`, `files`, `file_chunks`, `permissions`, `activity_logs`), `embeddings.json` search index, temp files |
| `SYNAPSE_STORAGE_DIR` | `storage/` | Content-addressed chunk store written by the C++ engine (`storage/<2 hex>/<sha256>`) |
| `SYNAPSE_ENGINE` | `src/cpp/build/synapse_engine` | Path to the C++ engine binary |
| `SYNAPSE_ENGINE_MODE` | `wsl` on Windows, `native` elsewhere | On Windows the Linux binary is run through `wsl.exe` |
| `SYNAPSE_CHUNK_SIZE` | engine default (1 MiB) | Chunk size in bytes |

The JSON tables use the same columns as `database/schema.sql`. The `data/` and `storage/` folders are git-ignored.

**C++ engine on Windows:** build it inside WSL (`cd src/cpp/build && cmake .. && make`). The backend calls it with `wsl.exe -e`. The first call after WSL has been idle takes several seconds while WSL starts. Downloads pass every chunk hash on one command line, which Windows caps at about 460 chunks per file (about 460 MB at 1 MiB per chunk). For larger files, raise `SYNAPSE_CHUNK_SIZE`.

**C++ file lock manager (`synapse_lock`):** an exclusive per-file lock used while a user edits a file on their computer. Source: `src/cpp/include/lock_manager.hpp`, `src/cpp/src/lock_manager.cpp`, `src/cpp/src/lock_main.cpp`.

- Build inside WSL with `sh src/cpp/build_lock.sh` (needs only `g++`), or with the `synapse_lock` CMake target. The binary goes to `src/cpp/build/synapse_lock`.
- Commands: `acquire <dir> <file_id> <user_id> <session_id> <lease_s> <abandon_s>`, `heartbeat <dir> <file_id> <user_id> <session_id> <lease_s>`, `release <dir> <file_id> <user_id> <session_id>`, `status <dir> <file_id>`. Each prints JSON. Exit codes: 0 ok, 1 error, 2 held by another user, 3 not the owner, 4 not locked.
- Mechanism: an exclusive `flock(2)` on `<file_id>.guard` serialises every decision, and the lock record `<file_id>.lock` is written to a temp file and renamed into place. The kernel drops the flock if a process dies. An unreadable record is treated as locked, never as free.
- Leases: a lock is *active* while heartbeats keep the lease fresh. Once the lease lapses it becomes *idle* but stays held. Only after `abandon_s` with no heartbeat can another user reclaim it, and the old session can then no longer save or release.
- Tests (inside WSL): `sh src/cpp/tests/test_lock.sh [/mnt/c/... or /tmp]`

**Locking API (FastAPI → `synapse_lock`):** every lock decision is made by the C++ binary. FastAPI keeps only session metadata in `data/edit_sessions.json`; lock records live in `data/locks/`.

| Endpoint | Purpose |
|---|---|
| `POST /api/workspace/files/{id}/open` | Needs write access. Acquires the exclusive lock and starts an editing session (or resumes your own). 409 if another user holds it. |
| `GET /api/workspace/files/{id}/lock` | Lock status: owner, state (active / idle / abandoned), times. |
| `GET /api/workspace/sessions` | Your active editing sessions. |
| `POST /api/workspace/sessions/heartbeat` | Renews all your locks. The dashboard calls it every minute. |
| `GET /api/workspace/sessions/{sid}/working-copy` | Downloads the working copy in its original format, to open in Word, Excel, a PDF reader and so on. The server file is not changed. |
| `POST /api/workspace/sessions/{sid}/save` | **Save to Synapse.** Multipart form with `file_id` and `file`. Checks the session, the C++ lock owner, write access and the file type. The upload is split into new chunks, then the file switches to the new version in one atomic write, and the lock is released. If anything fails, the original is unchanged and the lock is kept so you can retry. |
| `POST /api/workspace/sessions/{sid}/discard` | **Discard Changes.** Needs `{"confirm": true}`. The server file is untouched and the lock is released. |
| `POST /api/workspace/sessions/{sid}/release` | Releases without saving. Needs `{"confirm_unsaved": true}`, otherwise 409 with the unsaved-changes warning. |
| `POST/DELETE /api/workspace/files/{id}/share` | Owner shares a file with a username (`read` or `write`) or removes sharing. |

The server enforces these rules:
- With no access at all you get 404. With read-only access, opening returns 403.
- While another user holds the lock, a direct download returns 409.
- Only the lock owner's own session can save or release.
- If the lock manager can't be reached, access is denied (500) rather than allowed.

Lease settings: `SYNAPSE_LOCK_LEASE` (default 300 s) and `SYNAPSE_LOCK_ABANDON` (default 8 h).

**Dashboard (`/workspace-page`):**
- The sidebar has My Files, Recent Files (last 7 days), Starred, Shared With Me and Trash.
- Each file shows its type, size, owner, modified time and lock status.
- Row actions: Open, Download, Save to Synapse, Discard Changes, Release Lock, Share, Star, Trash/Restore.
- An *Editing on your computer* panel lists your open working copies. The page sends a lock heartbeat every minute and warns before you leave or log out with unsaved work.
- Clicking a file name shows a read-only preview for PDF, image, text and media files. There is no in-browser editor.
- **Create File** makes `.docx`, `.xlsx`, `.pptx` (valid blank documents from `src/py/office_templates.py`) and `.txt`, `.md`, `.csv`, `.json` (empty). To re-check the Office templates on a Windows machine with Microsoft Office, run `tests/verify_office_templates.ps1`.

**Versions:** each successful Save to Synapse creates a new file version (`files.json` → `version`). The chunks of earlier versions are kept, because chunks are never modified. They aren't shown in the UI yet.

**What a browser can and cannot do:** Synapse can't launch Word or Excel, and it can't detect when you press Save inside them. Open downloads a working copy, and your browser's download bar opens it in the default app. After editing, you choose **Save to Synapse** and select the edited file. Saving inside the desktop app only changes the copy on your computer.

**Workers:** the C++ locks and JSON writes are both safe across processes (JSON writes take an OS file lock on `data/.store.lock`). Login sessions are still kept in memory, though, so run a **single worker** until those are persisted:

```bash
uvicorn src.py.user_login:app --reload --host 0.0.0.0 --port 8000
```

Login and registration still send OTPs by email, so `SMTP_USER` and `SMTP_PASSWORD` must be set.

**Keyword search:** `GET /api/workspace/search?q=` searches file metadata only. Every word must match the file name (partial or complete), extension, file type or owner. Exact name matches rank first, then names that start with the query, then partial matches.

#### Search integration point (semantic search, teammate module)

Semantic search is developed separately in `embeddings/` and is not wired into the backend. To connect it later without rebuilding the search UI:

1. Expose an endpoint, e.g. `GET /api/semantic/search?q=`, that checks the session cookie and returns the same JSON item shape as `GET /api/workspace/files`, best match first. Extra fields such as `score` and `snippet` are allowed.
2. In `template/workspace.html`, register it with the built-in provider hook:

```js
SynapseSearch.register("semantic", q =>
    fetch(`/api/semantic/search?q=${encodeURIComponent(q)}`, { credentials: "same-origin" })
        .then(r => r.json()));
SynapseSearch.use("semantic");   // SynapseSearch.use("keyword") switches back
```

Tests: `python -m unittest discover -s tests -v`

### Step 4: Configure PostgreSQL (only for `SYNAPSE_STORAGE=postgres`)

1. Install and start PostgreSQL.
2. Create the project database.
3. Install and enable the pgvector extension if required.
4. Configure the database connection using the project's environment variables or configuration file.
5. Apply the database schema or migrations provided by the project.

Example SQL for enabling pgvector:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Use the actual database name, username, and password configured for your environment.

### Step 5: Start the Backend

If the project contains a FastAPI application at `main.py`, start it with:

```bash
uvicorn main:app --reload
```

If the application is located in a package such as `backend/main.py`, use the corresponding import path:

```bash
uvicorn backend.main:app --reload
```

The correct command depends on the repository's actual folder structure.

### Step 6: Access the Application

Open the local address configured for the frontend or backend in your browser. FastAPI's interactive API documentation is generally available at:

```text
http://127.0.0.1:8000/docs
```

For LAN access, the server must be configured to listen on the appropriate network interface, and firewall rules must permit the required connections.

**Important:** The commands above are a setup template. Confirm the actual entry point, dependencies, database configuration, and frontend instructions in the repository before running the application.

## 8. Current Project Status

### Completed / Established
- Project requirements and objectives defined.
- High-level system architecture designed.
- Core technologies and database approach selected.
- Initial study of multi-user and distributed file systems completed.
- OS, DBMS, and AI/ML modules identified.

### Under Development / Planned
- Completion and integration of frontend and backend modules.
- File upload, download, and storage workflows.
- Chunk-based file processing and OS-level concurrency handling.
- Database integration for file metadata and permissions.
- Semantic search using embeddings and pgvector.
- Administrative monitoring and performance evaluation.

The exact status of individual features should be updated as implementation progresses.

## 9. Future Scope

- Improve storage efficiency and caching.
- Optimize concurrent file access.
- Enhance semantic search accuracy.
- Introduce intelligent file categorization and access prediction.
- Strengthen security and permission management.
- Test the system with multiple clients connected through a LAN.
- Evaluate response time, memory usage, and concurrent request handling.

## 10. Expected Outcome

Synapse aims to provide a functional LAN-based file management platform that combines centralized storage, secure multi-user access, efficient file operations, database-backed metadata management, and intelligent file search.

The project demonstrates the practical application of Operating Systems, DBMS, and AI/ML concepts in an integrated software system.

