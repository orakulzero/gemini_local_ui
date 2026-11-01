"""
backend/app/storage/database.py
Thread-safe, high-performance asynchronous SQLite connection manager with WAL mode,
reader connection pooling, single-dispatch execution, and serialized write locking.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
import time
from typing import Any
from collections.abc import AsyncIterator, Callable

import aiosqlite

logger = logging.getLogger("gemini_storage.database")

IS_WINDOWS = sys.platform == "win32"

_MODIFYING_SQL_RE = re.compile(
    r"^\s*(?:--[^\n]*(?:\n|$)|/\*.*?\*/\s*)*(?:WITH\s+.*?\s+)?(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER)\b",
    re.IGNORECASE | re.DOTALL,
)


class AsyncRLock:
    """Async reentrant lock bound to the active asyncio.Task."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._count = 0

    async def acquire(self) -> None:
        me = asyncio.current_task()
        if self._owner == me:
            self._count += 1
            return
        await self._lock.acquire()
        self._owner = me
        self._count = 1

    def release(self) -> None:
        me = asyncio.current_task()
        if self._owner != me:
            raise RuntimeError("Cannot release un-acquired lock")
        self._count -= 1
        if self._count == 0:
            self._owner = None
            self._lock.release()

    def is_owner(self) -> bool:
        return self._owner == asyncio.current_task()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.release()


def get_default_db_path() -> Path:
    """Returns the platform-standard database file path."""
    if IS_WINDOWS:
        base_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "gemini_local_ui"
    else:
        base_dir = Path.home() / ".gemini_local_ui"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "gemini_local.db"


class Database:
    """
    High-performance asynchronous SQLite Database Manager:
    - Dedicated aiosqlite.Connection for serialized write transactions.
    - ThreadPoolExecutor reader connection pool for non-blocking parallel reads.
    - Single-dispatch execution for fetch_all, fetch_one, and fetch_val.
    - WAL mode, 16MB cache, memory temp store, 64MB mmap.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        max_readers: int | None = None,
    ) -> None:
        if db_path is None:
            env_path = os.environ.get("GEMINI_DB_PATH")
            self.db_path = Path(env_path) if env_path else get_default_db_path()
        elif str(db_path) == ":memory:":
            self.db_path = ":memory:"
        else:
            self.db_path = Path(db_path)
            if self.db_path.parent:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db: aiosqlite.Connection | None = None
        self._write_lock = AsyncRLock()
        self._tx_depth: dict[asyncio.Task, int] = {}
        self._initialized = False
        self.is_memory = str(self.db_path) == ":memory:"

        # Optimal reader pool scaling: default 2-4 workers on desktop, env override GEMINI_DB_MAX_READERS
        if max_readers is not None:
            self.max_readers = int(max_readers)
        else:
            env_readers = os.environ.get("GEMINI_DB_MAX_READERS")
            if env_readers:
                self.max_readers = int(env_readers)
            else:
                self.max_readers = min(4, max(2, (os.cpu_count() or 4) // 2))

        # ThreadPool reader pool & connection registry
        self._reader_executor: ThreadPoolExecutor | None = None
        self._local = threading.local()
        self._reader_conns: list[sqlite3.Connection] = []
        self._reader_lock = threading.Lock()
        self._mutation_listeners: list[Callable[[], Any]] = []

    def register_mutation_listener(self, listener: Callable[[], Any]) -> None:
        """Registers a callback invoked whenever modifying statements commit."""
        self._mutation_listeners.append(listener)

    def _notify_mutations(self) -> None:
        """Notifies registered listeners that database state has changed."""
        for listener in self._mutation_listeners:
            try:
                res = listener()
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
            except Exception as e:
                logger.warning("Error in mutation listener: %s", e)

    def _get_sync_reader_conn(self) -> sqlite3.Connection:
        """Returns thread-local cached synchronous reader connection in autocommit mode."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            if not self.is_memory and isinstance(self.db_path, Path) and self.db_path.parent:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path), timeout=5.0, check_same_thread=False, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            conn.execute("PRAGMA journal_size_limit = 67108864;")
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA cache_size = -16000;")
            conn.execute("PRAGMA temp_store = MEMORY;")
            conn.execute("PRAGMA mmap_size = 67108864;")
            self._local.conn = conn
            with self._reader_lock:
                self._reader_conns.append(conn)
        return self._local.conn

    def _ensure_reader_pool(self) -> ThreadPoolExecutor:
        """Lazily initializes the reader ThreadPoolExecutor."""
        if self._reader_executor is None:
            self._reader_executor = ThreadPoolExecutor(
                max_workers=self.max_readers,
                thread_name_prefix="sqlite_reader_",
            )
        return self._reader_executor

    async def connect(self) -> aiosqlite.Connection:
        """Establishes or returns primary write database connection in autocommit mode."""
        if self._db is None:
            if not self.is_memory and isinstance(self.db_path, Path) and self.db_path.parent:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            target = str(self.db_path)
            self._db = await aiosqlite.connect(target, isolation_level=None)
            self._db.row_factory = aiosqlite.Row

            await self._db.execute("PRAGMA journal_mode = WAL;")
            await self._db.execute("PRAGMA synchronous = NORMAL;")
            await self._db.execute("PRAGMA busy_timeout = 5000;")
            await self._db.execute("PRAGMA journal_size_limit = 67108864;")
            await self._db.execute("PRAGMA foreign_keys = ON;")
            await self._db.execute("PRAGMA cache_size = -16000;")
            await self._db.execute("PRAGMA temp_store = MEMORY;")
            await self._db.execute("PRAGMA mmap_size = 67108864;")
            self._ensure_reader_pool()
            logger.info("Connected to SQLite database at %s (WAL mode, autocommit, FK=ON)", target)

        return self._db

    async def close(self) -> None:
        """Gracefully closes write connection, drains reader pool, and closes all reader connections."""
        if self._reader_executor is not None:
            self._reader_executor.shutdown(wait=True, cancel_futures=True)
            self._reader_executor = None

        with self._reader_lock:
            for conn in self._reader_conns:
                try:
                    conn.close()
                except Exception as e:
                    logger.warning("Error closing reader connection: %s", e)
            self._reader_conns.clear()

        self._local = threading.local()

        if self._db is not None:
            await self._db.close()
            self._db = None
            self._initialized = False
            logger.info("Database connection closed cleanly with zero leaks")

    async def init_schema(self, schema_sql_path: str | Path | None = None) -> None:
        """Executes schema.sql DDL to initialize database tables and triggers."""
        db = await self.connect()
        if schema_sql_path is None:
            schema_sql_path = Path(__file__).parent / "schema.sql"

        with open(schema_sql_path, encoding="utf-8") as f:
            ddl = f.read()

        async with self._write_lock:
            await db.executescript(ddl)
            await db.commit()

        self._initialized = True
        self._notify_mutations()
        logger.info("Database schema initialized successfully from %s", schema_sql_path)

    async def execute(
        self,
        query: str,
        params: tuple[Any, ...] | dict[str, Any] = (),
    ) -> aiosqlite.Cursor:
        """Executes SQL statement with write-lock protection for modifying commands."""
        db = await self.connect()
        is_modifying = bool(_MODIFYING_SQL_RE.match(query))

        if is_modifying:
            task = asyncio.current_task()
            async with self._write_lock:
                cursor = await db.execute(query, params)
                in_tx = task is not None and self._tx_depth.get(task, 0) > 0
                if not in_tx:
                    await db.commit()
                    self._notify_mutations()
                return cursor
        else:
            return await db.execute(query, params)

    @asynccontextmanager
    async def write_transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """
        Async context manager for atomic write transactions:
        - Acquires in-process _write_lock.
        - Issues BEGIN IMMEDIATE to acquire RESERVED write lock immediately,
          eliminating SHARED->RESERVED lock upgrade deadlocks.
        - Emits COMMIT on clean exit, ROLLBACK on BaseException (including CancelledError).
        """
        db = await self.connect()
        task = asyncio.current_task()
        async with self._write_lock:
            depth = self._tx_depth.get(task, 0) if task is not None else 0
            if task is not None:
                self._tx_depth[task] = depth + 1
            if depth == 0:
                await db.execute("BEGIN IMMEDIATE;")
            try:
                yield db
                if task is not None and self._tx_depth.get(task, 0) == 1:
                    await db.execute("COMMIT;")
                    self._notify_mutations()
            except BaseException:
                if task is not None and self._tx_depth.get(task, 0) == 1:
                    try:
                        await db.execute("ROLLBACK;")
                    except Exception as rb_err:
                        logger.warning("Error rolling back write transaction: %s", rb_err)
                raise
            finally:
                if task is not None:
                    curr = self._tx_depth.get(task, 1) - 1
                    if curr <= 0:
                        self._tx_depth.pop(task, None)
                    else:
                        self._tx_depth[task] = curr

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Backwards-compatible alias routing to write_transaction."""
        async with self.write_transaction() as conn:
            yield conn


    def _sync_fetch_all(self, query: str, params: Any) -> list[dict[str, Any]]:
        conn = self._get_sync_reader_conn()
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def _sync_fetch_one(self, query: str, params: Any) -> dict[str, Any] | None:
        conn = self._get_sync_reader_conn()
        cursor = conn.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row is not None else None

    def _sync_fetch_val(self, query: str, params: Any) -> Any:
        conn = self._get_sync_reader_conn()
        cursor = conn.execute(query, params)
        row = cursor.fetchone()
        return row[0] if row is not None else None

    async def fetch_all(
        self,
        query: str,
        params: tuple[Any, ...] | dict[str, Any] = (),
    ) -> list[dict[str, Any]]:
        """Executes a query and returns all matching rows as dictionaries."""
        if self.is_memory:
            db = await self.connect()
            rows = await db.execute_fetchall(query, params)
            return [dict(row) for row in rows]

        pool = self._ensure_reader_pool()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(pool, self._sync_fetch_all, query, params)

    async def fetch_one(
        self,
        query: str,
        params: tuple[Any, ...] | dict[str, Any] = (),
    ) -> dict[str, Any] | None:
        """Executes a query and returns a single row as a dictionary, or None."""
        if self.is_memory:
            db = await self.connect()
            rows = await db.execute_fetchall(query, params)
            if not rows:
                return None
            return dict(rows[0])

        pool = self._ensure_reader_pool()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(pool, self._sync_fetch_one, query, params)

    async def fetch_val(
        self,
        query: str,
        params: tuple[Any, ...] | dict[str, Any] = (),
    ) -> Any:
        """Executes a query and returns the scalar value of the first column, or None."""
        if self.is_memory:
            db = await self.connect()
            rows = await db.execute_fetchall(query, params)
            if not rows:
                return None
            return rows[0][0]

        pool = self._ensure_reader_pool()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(pool, self._sync_fetch_val, query, params)

    def get_sync_connection(self) -> sqlite3.Connection:
        """Creates a standalone synchronous SQLite connection if needed in autocommit mode."""
        target = str(self.db_path)
        conn = sqlite3.connect(target, timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA journal_size_limit = 67108864;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA cache_size = -16000;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA mmap_size = 67108864;")
        return conn

    async def record_telemetry(
        self,
        account_id: str,
        model_id: str,
        model_name: str,
        feature_mode: str = "chat",
        reasoning_level: int = 1,
        request_timestamp: int | None = None,
        prompt_tokens_est: int = 0,
        completion_tokens_est: int = 0,
        thinking_tokens_est: int = 0,
        status_code: int = 200,
        error_code: str | None = None,
        duration_ms: int = 0,
        was_failover: int = 0,
        failover_from_account: str | None = None,
    ) -> None:
        """Records a single request transaction in usage_telemetry."""
        if request_timestamp is None:
            request_timestamp = int(time.time() * 1000)
        total_tokens_est = prompt_tokens_est + completion_tokens_est + thinking_tokens_est

        query = """
        INSERT INTO usage_telemetry (
            account_id, model_id, model_name, feature_mode, reasoning_level,
            request_timestamp, prompt_tokens_est, completion_tokens_est,
            thinking_tokens_est, total_tokens_est, status_code, error_code,
            duration_ms, was_failover, failover_from_account
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        await self.execute(
            query,
            (
                account_id,
                model_id,
                model_name,
                feature_mode,
                reasoning_level,
                request_timestamp,
                prompt_tokens_est,
                completion_tokens_est,
                thinking_tokens_est,
                total_tokens_est,
                status_code,
                error_code,
                duration_ms,
                was_failover,
                failover_from_account,
            ),
        )

    async def log_usage_telemetry(self, *args: Any, **kwargs: Any) -> None:
        """Alias for record_telemetry."""
        await self.record_telemetry(*args, **kwargs)

    async def get_rolling_usage(
        self,
        account_id: str | None = None,
        window_seconds: int = 86400,
    ) -> dict[str, Any]:
        """Calculates aggregated usage metrics across a rolling time window (default 24h)."""
        window_start_ms = int((time.time() - window_seconds) * 1000)
        if account_id:
            query = """
            SELECT 
                COUNT(*) as total_requests,
                COALESCE(SUM(prompt_tokens_est), 0) as total_prompt_tokens,
                COALESCE(SUM(completion_tokens_est), 0) as total_completion_tokens,
                COALESCE(SUM(thinking_tokens_est), 0) as total_thinking_tokens,
                COALESCE(SUM(total_tokens_est), 0) as total_tokens,
                COALESCE(SUM(CASE WHEN feature_mode = 'task' THEN 1 ELSE 0 END), 0) as task_mode_calls,
                COALESCE(SUM(CASE WHEN status_code IN (1037, 429, 1061) THEN 1 ELSE 0 END), 0) as quota_error_count,
                COALESCE(SUM(CASE WHEN was_failover = 1 THEN 1 ELSE 0 END), 0) as failover_events_count
            FROM usage_telemetry
            WHERE request_timestamp >= ? AND account_id = ?
            """
            row = await self.fetch_one(query, (window_start_ms, account_id))
        else:
            query = """
            SELECT 
                COUNT(*) as total_requests,
                COALESCE(SUM(prompt_tokens_est), 0) as total_prompt_tokens,
                COALESCE(SUM(completion_tokens_est), 0) as total_completion_tokens,
                COALESCE(SUM(thinking_tokens_est), 0) as total_thinking_tokens,
                COALESCE(SUM(total_tokens_est), 0) as total_tokens,
                COALESCE(SUM(CASE WHEN feature_mode = 'task' THEN 1 ELSE 0 END), 0) as task_mode_calls,
                COALESCE(SUM(CASE WHEN status_code IN (1037, 429, 1061) THEN 1 ELSE 0 END), 0) as quota_error_count,
                COALESCE(SUM(CASE WHEN was_failover = 1 THEN 1 ELSE 0 END), 0) as failover_events_count
            FROM usage_telemetry
            WHERE request_timestamp >= ?
            """
            row = await self.fetch_one(query, (window_start_ms,))

        if not row:
            return {
                "total_requests": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_thinking_tokens": 0,
                "total_tokens": 0,
                "task_mode_calls": 0,
                "quota_error_count": 0,
                "failover_events_count": 0,
            }
        return dict(row)

    async def get_per_model_usage(
        self,
        account_id: str,
        window_seconds: int = 86400,
    ) -> dict[str, dict[str, Any]]:
        """Calculates per-model request counts and token sums for an account in rolling window."""
        window_start_ms = int((time.time() - window_seconds) * 1000)
        query = """
        SELECT 
            model_name,
            model_id,
            COUNT(*) as requests,
            COALESCE(SUM(total_tokens_est), 0) as tokens_est,
            COALESCE(SUM(CASE WHEN status_code IN (1037, 429, 1061) THEN 1 ELSE 0 END), 0) as error_count
        FROM usage_telemetry
        WHERE account_id = ? AND request_timestamp >= ?
        GROUP BY model_name, model_id
        """
        rows = await self.fetch_all(query, (account_id, window_start_ms))
        result: dict[str, dict[str, Any]] = {}
        for r in rows:
            name = r.get("model_name") or r.get("model_id")
            result[name] = {
                "model_id": r.get("model_id"),
                "requests": r.get("requests", 0),
                "tokens_est": r.get("tokens_est", 0),
                "error_count": r.get("error_count", 0),
            }
        return result

    async def get_account_limits_config(self, account_id: str) -> dict[str, Any]:
        """Loads warning and soft limits config for account, returning sensible defaults if unset."""
        query = "SELECT * FROM account_limits_config WHERE account_id = ?"
        row = await self.fetch_one(query, (account_id,))
        if row:
            return dict(row)
        return {
            "account_id": account_id,
            "daily_request_threshold": 250,
            "daily_token_threshold": 500000,
            "hourly_request_threshold": 60,
            "cooldown_until": None,
            "cooldown_reason": None,
            "updated_at": int(time.time() * 1000),
        }

    async def set_account_limits_config(
        self,
        account_id: str,
        daily_request_threshold: int = 250,
        daily_token_threshold: int = 500000,
        hourly_request_threshold: int = 60,
        cooldown_until: int | None = None,
        cooldown_reason: str | None = None,
    ) -> None:
        """Updates soft limits and warning thresholds for an account."""
        now_ms = int(time.time() * 1000)
        query = """
        INSERT INTO account_limits_config (
            account_id, daily_request_threshold, daily_token_threshold,
            hourly_request_threshold, cooldown_until, cooldown_reason, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            daily_request_threshold = excluded.daily_request_threshold,
            daily_token_threshold = excluded.daily_token_threshold,
            hourly_request_threshold = excluded.hourly_request_threshold,
            cooldown_until = excluded.cooldown_until,
            cooldown_reason = excluded.cooldown_reason,
            updated_at = excluded.updated_at
        """
        await self.execute(
            query,
            (
                account_id,
                daily_request_threshold,
                daily_token_threshold,
                hourly_request_threshold,
                cooldown_until,
                cooldown_reason,
                now_ms,
            ),
        )

    async def get_recent_failovers(self, limit: int = 10) -> list[dict[str, Any]]:
        """Returns recent failover audit records from usage_telemetry."""
        query = """
        SELECT request_timestamp, account_id, failover_from_account, model_name, status_code, error_code
        FROM usage_telemetry
        WHERE was_failover = 1
        ORDER BY request_timestamp DESC
        LIMIT ?
        """
        return await self.fetch_all(query, (limit,))
