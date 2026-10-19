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

### Step 4: Configure PostgreSQL

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

