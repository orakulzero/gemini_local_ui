-- backend/app/storage/schema.sql
-- Normalized relational tables, FTS5 external content virtual table, and zero-maintenance triggers.

PRAGMA foreign_keys = ON;

-- 1. Conversations Table
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT 'gemini-2.5-pro',
    remote_conversation_id TEXT UNIQUE,
    gem_id TEXT,
    system_prompt TEXT,
    active_leaf_message_id INTEGER,
    is_pinned INTEGER NOT NULL DEFAULT 0 CHECK(is_pinned IN (0, 1)),
    is_archived INTEGER NOT NULL DEFAULT 0 CHECK(is_archived IN (0, 1)),
    created_at INTEGER NOT NULL,     -- Unix epoch milliseconds
    updated_at INTEGER NOT NULL,     -- Unix epoch milliseconds
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_remote ON conversations(remote_conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversations_archived ON conversations(is_archived);

-- 2. Messages Table (DAG tree model with parent_id)
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES messages(id) ON DELETE SET NULL CHECK(parent_id != id),
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    thoughts TEXT,
    model TEXT NOT NULL DEFAULT 'gemini-2.5-pro',
    token_count INTEGER,
    is_error INTEGER NOT NULL DEFAULT 0 CHECK(is_error IN (0, 1)),
    created_at INTEGER NOT NULL,     -- Unix epoch milliseconds
    updated_at INTEGER NOT NULL,     -- Unix epoch milliseconds
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_conv_created ON messages(conversation_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_messages_parent ON messages(parent_id);
CREATE INDEX IF NOT EXISTS idx_messages_conv_parent ON messages(conversation_id, parent_id);

-- 3. Attachments Table
CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    storage_path TEXT NOT NULL DEFAULT '',
    uri TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL      -- Unix epoch milliseconds
);

CREATE INDEX IF NOT EXISTS idx_attachments_msg ON attachments(message_id);

-- 4. FTS5 External Content Virtual Table
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    thoughts,
    content='messages',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- 5. Automated FTS5 Synchronization Triggers
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, thoughts)
    VALUES (new.id, new.content, new.thoughts);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, thoughts)
    VALUES ('delete', old.id, old.content, old.thoughts);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, thoughts)
    VALUES ('delete', old.id, old.content, old.thoughts);
    INSERT INTO messages_fts(rowid, content, thoughts)
    VALUES (new.id, new.content, new.thoughts);
END;

-- 6. Usage Telemetry Table
CREATE TABLE IF NOT EXISTS usage_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    feature_mode TEXT NOT NULL DEFAULT 'chat' CHECK(feature_mode IN ('chat', 'task')),
    reasoning_level INTEGER NOT NULL DEFAULT 1,
    request_timestamp INTEGER NOT NULL,          -- Unix epoch milliseconds
    prompt_tokens_est INTEGER NOT NULL DEFAULT 0,
    completion_tokens_est INTEGER NOT NULL DEFAULT 0,
    thinking_tokens_est INTEGER NOT NULL DEFAULT 0,
    total_tokens_est INTEGER NOT NULL DEFAULT 0,
    status_code INTEGER NOT NULL DEFAULT 200,    -- 200, 1037, 429, 1061, 500
    error_code TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    was_failover INTEGER NOT NULL DEFAULT 0 CHECK(was_failover IN (0, 1)),
    failover_from_account TEXT
);

CREATE INDEX IF NOT EXISTS idx_telemetry_acc_time 
    ON usage_telemetry(account_id, request_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_model_time 
    ON usage_telemetry(model_id, request_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_time 
    ON usage_telemetry(request_timestamp DESC);

-- 7. Account Warning & Soft Limits Configuration Table
CREATE TABLE IF NOT EXISTS account_limits_config (
    account_id TEXT PRIMARY KEY,
    daily_request_threshold INTEGER NOT NULL DEFAULT 250,
    daily_token_threshold INTEGER NOT NULL DEFAULT 500000,
    hourly_request_threshold INTEGER NOT NULL DEFAULT 60,
    cooldown_until INTEGER,                      -- Unix epoch milliseconds
    cooldown_reason TEXT,
    updated_at INTEGER NOT NULL
);

