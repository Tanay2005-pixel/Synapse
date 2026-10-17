
-- Synapse File Management System
-- PostgreSQL database schema

CREATE TABLE users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username VARCHAR(50) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE files (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id BIGINT NOT NULL REFERENCES users(id),
    file_name VARCHAR(255) NOT NULL,
    file_size BIGINT NOT NULL CHECK (file_size >= 0),
    mime_type VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE file_chunks (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id BIGINT NOT NULL REFERENCES files(id)
        ON DELETE CASCADE,
    chunk_index INT NOT NULL CHECK (chunk_index >= 0),
    chunk_hash CHAR(64) NOT NULL,
    chunk_size BIGINT NOT NULL CHECK (chunk_size >= 0),
    storage_path TEXT NOT NULL,
    UNIQUE (file_id, chunk_index)
);

CREATE TABLE permissions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id BIGINT NOT NULL REFERENCES files(id)
        ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id)
        ON DELETE CASCADE,
    permission VARCHAR(10) NOT NULL
        CHECK (permission IN ('read', 'write')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (file_id, user_id)
);

CREATE TABLE activity_logs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT REFERENCES users(id)
        ON DELETE SET NULL,
    file_id BIGINT REFERENCES files(id)
        ON DELETE SET NULL,
    action VARCHAR(50) NOT NULL,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common queries

CREATE INDEX idx_files_owner
ON files(owner_id);

CREATE INDEX idx_permissions_user
ON permissions(user_id);

CREATE INDEX idx_activity_created
ON activity_logs(created_at);
