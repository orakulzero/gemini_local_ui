"""
backend/tests/test_m2_challenge_search.py
Adversarial empirical stress suite for Milestone M2:
1. FTS5 Query Injection & Malformed Syntax stress tests (unclosed quotes, nested booleans, code snippets, regex, null bytes).
2. FTS5 Trigger Integrity (insert, update, delete, cascade conversation delete, zero orphaned hits, FTS5 integrity-check).
3. 100 Concurrent Search Queries benchmark on 1,000 conversations / 11,000 messages with P99 latency SLA check (< 50ms).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import random
import time
from typing import AsyncIterator, List
import pytest
import pytest_asyncio

from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.search import SearchEngine, sanitize_fts5_query


@pytest_asyncio.fixture
async def challenge_db(tmp_path: Path) -> AsyncIterator[Database]:
    """Provides an isolated disk-backed SQLite database with full schema and WAL mode."""
    db_file = tmp_path / "challenge_gemini.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    await db.init_schema(schema_path)
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_fts5_query_injection_and_malformed_syntax(challenge_db: Database) -> None:
    """
    Adversarial Challenge 1:
    Feeds malformed syntax, injection payloads, code snippets, regex, unclosed quotes,
    and null bytes to both sanitize_fts5_query and SearchEngine.search.
    Asserts zero unhandled exceptions or crashes.
    """
    search_engine = SearchEngine(challenge_db)
    repo = ConversationRepository(challenge_db)

    # Seed a target conversation and message with various code/text tokens
    conv = await repo.create_conversation(title="Adversarial Target", conversation_id="conv_adv_01")
    await repo.add_message(
        conversation_id=conv.id,
        role="assistant",
        content="Testing std::vector<std::string> *ptr = &val->foo; NullPointerException in regex ^[a-z]+$",
        thoughts="Internal reasoning regarding C++20 and memory-safety.",
    )

    malformed_queries = [
        # Unclosed quotes & weird quote chains
        '"""',
        '"',
        '"""""',
        '"unclosed quote',
        'unclosed"quote"',
        '""foo""bar""',
        '""""""hello""""""',
        '"" "" ""',
        "'''''",
        "''''test''''",
        
        # Nested boolean operators & SQLite FTS5 reserved keywords
        "AND NOT (OR)",
        "OR OR OR",
        "NOT NOT NOT",
        "(AND (OR (NOT)))",
        "AND",
        "OR",
        "NOT",
        "NEAR()",
        "NEAR(a b, 10)",
        "NEAR/5(foo bar)",
        "MATCH",
        "RANK",
        "bm25",
        "snippet",
        "AND OR NOT AND OR NOT",
        "(((foo AND) OR (NOT)))",

        # Raw code snippets with punctuation & operators
        "std::vector<std::string>",
        "auto ptr = *foo->bar;",
        "template <typename T>",
        "fn(&mut self)",
        "x::y::z()",
        "arr[0]->next",
        "int* ptr = &val;",
        "SELECT * FROM messages WHERE 1=1;",
        "class Foo<T>: public Bar<T> {}",
        "console.log(`Template string ${arg}`);",
        "System.out.println(null);",
        "Error: NullPointerException at com.app.Main.init(Main.java:42)",

        # Regex patterns & special metacharacters
        r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$",
        r"(?<=foo)bar",
        r"(?P<name>\w+)",
        r".*+?{}",
        r"\d{3}-\d{2}-\d{4}",
        r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+",
        r"\b(foo|bar|baz)\b",

        # SQL Injection / FTS escape attempts
        "' OR '1'='1",
        "'; DROP TABLE messages; --",
        "1' UNION SELECT * FROM conversations--",
        "test' OR 1=1;--",
        "content:test",
        "thoughts:reasoning",
        "messages_fts:secret",
        "{col1 col2}:query",

        # Null bytes & binary control characters
        "\x00",
        "foo\x00bar",
        "\x00\x00\x00",
        "\x01\x02\x03\x04\x05",
        "query\x00with\x00nulls",
        "test\x1b[31mRed\x1b[0m",
        "\ufffe\uffff",
        "\u200b\u200c\u200d\ufeff",
        "\u202e\u202doverride",
        "   \t\r\n   ",
    ]

    for q in malformed_queries:
        # 1. Verify query sanitizer never raises exception
        try:
            sanitized = sanitize_fts5_query(q)
            assert isinstance(sanitized, str)
        except Exception as exc:
            pytest.fail(f"sanitize_fts5_query raised unexpected exception on query {q!r}: {exc}")

        # 2. Verify SearchEngine.search handles query without crashing or unhandled error
        try:
            results = await search_engine.search(q, limit=10)
            assert isinstance(results, list)
        except Exception as exc:
            pytest.fail(f"SearchEngine.search raised unhandled exception on query {q!r}: {exc}")

    # 3. Test boundary parameter values and extreme payloads
    boundary_cases = [
        {"query": "test", "limit": 0, "offset": 0, "conversation_id": None},
        {"query": "test", "limit": -1, "offset": -1, "conversation_id": None},
        {"query": "test", "limit": 10000, "offset": 5000, "conversation_id": None},
        {"query": "test", "limit": 20, "offset": 0, "conversation_id": "nonexistent_conv_id"},
        {"query": "test", "limit": 20, "offset": 0, "conversation_id": "' OR '1'='1"},
        {"query": "test", "limit": 20, "offset": 0, "conversation_id": "\x00invalid"},
        {"query": "a" * 5000, "limit": 10, "offset": 0, "conversation_id": None},  # Giant single token
        {"query": ("token " * 500) + "end", "limit": 10, "offset": 0, "conversation_id": None},  # 500 tokens
        {"query": None, "limit": 20, "offset": 0, "conversation_id": None},  # type: ignore[arg-type]
    ]

    for b in boundary_cases:
        try:
            res = await search_engine.search(
                query=b["query"],
                limit=b["limit"],
                offset=b["offset"],
                conversation_id=b["conversation_id"],
            )
            assert isinstance(res, list)
        except Exception as exc:
            pytest.fail(f"SearchEngine.search crashed on boundary case {b}: {exc}")



@pytest.mark.asyncio
async def test_fts5_trigger_integrity_and_cascade_cleanup(challenge_db: Database) -> None:
    """
    Adversarial Challenge 2:
    Verifies message insertion, update, deletion, and cascading conversation deletion
    maintain 100% sync in messages_fts with zero orphaned search hits.
    Also executes FTS5 native 'integrity-check'.
    """
    search_engine = SearchEngine(challenge_db)
    repo = ConversationRepository(challenge_db)

    # 1. Create two separate conversations
    conv_a = await repo.create_conversation(title="Conversation Alpha", conversation_id="conv_alpha")
    conv_b = await repo.create_conversation(title="Conversation Beta", conversation_id="conv_beta")

    # 2. Insert messages with unique canary tokens
    msg_a1 = await repo.add_message(
        conversation_id=conv_a.id,
        role="user",
        content="CanaryAlphaUniqueTokenAlpha content body.",
        thoughts="CanaryAlphaThoughtUniqueToken reasoning trace.",
    )
    msg_a2 = await repo.add_message(
        conversation_id=conv_a.id,
        role="assistant",
        content="CanaryAlphaSecondMessage content text.",
        thoughts="CanaryAlphaSecondThought reasoning trace.",
    )
    msg_b1 = await repo.add_message(
        conversation_id=conv_b.id,
        role="user",
        content="CanaryBetaUniqueTokenBeta content body.",
        thoughts="CanaryBetaThoughtUniqueToken reasoning trace.",
    )

    # Assert all canaries indexed in messages_fts immediately
    res_a1 = await search_engine.search("CanaryAlphaUniqueTokenAlpha")
    assert len(res_a1) == 1
    assert res_a1[0].message_id == msg_a1.id

    res_a1_th = await search_engine.search("CanaryAlphaThoughtUniqueToken")
    assert len(res_a1_th) == 1
    assert res_a1_th[0].message_id == msg_a1.id

    res_b1 = await search_engine.search("CanaryBetaUniqueTokenBeta")
    assert len(res_b1) == 1
    assert res_b1[0].message_id == msg_b1.id

    # 3. Test UPDATE sync on msg_a1 (modify content and thoughts)
    await challenge_db.execute(
        "UPDATE messages SET content = ?, thoughts = ? WHERE id = ?;",
        ("UpdatedCanaryAlphaTokenReplacement body.", "UpdatedCanaryAlphaThoughtReplacement trace.", msg_a1.id),
    )

    # Old tokens must return 0 hits
    assert len(await search_engine.search("CanaryAlphaUniqueTokenAlpha")) == 0
    assert len(await search_engine.search("CanaryAlphaThoughtUniqueToken")) == 0

    # New tokens must match
    res_updated_content = await search_engine.search("UpdatedCanaryAlphaTokenReplacement")
    assert len(res_updated_content) == 1
    assert res_updated_content[0].message_id == msg_a1.id

    res_updated_thoughts = await search_engine.search("UpdatedCanaryAlphaThoughtReplacement")
    assert len(res_updated_thoughts) == 1
    assert res_updated_thoughts[0].message_id == msg_a1.id

    # 4. Test Single Message DELETE
    await challenge_db.execute("DELETE FROM messages WHERE id = ?;", (msg_a1.id,))

    # Verify deleted message produces 0 hits in search
    assert len(await search_engine.search("UpdatedCanaryAlphaTokenReplacement")) == 0
    assert len(await search_engine.search("UpdatedCanaryAlphaThoughtReplacement")) == 0

    # Verify directly against messages_fts virtual table (no JOINs)
    raw_hits = await challenge_db.fetch_all(
        "SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'UpdatedCanaryAlphaTokenReplacement';"
    )
    assert len(raw_hits) == 0, f"Orphaned row found in messages_fts: {raw_hits}"

    # 5. Test Cascading Conversation DELETE
    # Delete conv_b, which should cascade delete msg_b1
    deleted_b = await repo.delete_conversation(conv_b.id)
    assert deleted_b is True

    # Check conversation and message tables
    assert await repo.get_conversation(conv_b.id) is None
    msgs_in_db = await challenge_db.fetch_all("SELECT * FROM messages WHERE conversation_id = ?;", (conv_b.id,))
    assert len(msgs_in_db) == 0

    # Verify search returns 0 hits
    assert len(await search_engine.search("CanaryBetaUniqueTokenBeta")) == 0
    assert len(await search_engine.search("CanaryBetaThoughtUniqueToken")) == 0

    # Verify directly against messages_fts virtual table to prove no orphaned FTS index entries exist
    raw_fts_orphans = await challenge_db.fetch_all(
        "SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'CanaryBetaUniqueTokenBeta';"
    )
    assert len(raw_fts_orphans) == 0, f"Cascade deletion left orphaned entry in messages_fts: {raw_fts_orphans}"

    # Verify total row count consistency: messages_fts row count must exactly equal messages table row count
    # Note: external content FTS table rowid count is checked via SELECT count(rowid) FROM messages_fts
    fts_rows = await challenge_db.fetch_all("SELECT count(rowid) AS cnt FROM messages_fts;")
    real_rows = await challenge_db.fetch_all("SELECT count(*) AS cnt FROM messages;")
    assert fts_rows[0]["cnt"] == real_rows[0]["cnt"], (
        f"Row count mismatch! messages table: {real_rows[0]['cnt']}, messages_fts: {fts_rows[0]['cnt']}"
    )

    # 6. Native SQLite FTS5 integrity-check command
    # FTS5 will raise an error if any token index entry is invalid or out of sync
    await challenge_db.execute("INSERT INTO messages_fts(messages_fts) VALUES('integrity-check');")


@pytest.mark.asyncio
async def test_benchmark_query_latency_under_concurrency(challenge_db: Database) -> None:
    """
    Adversarial Challenge 3:
    Benchmark Query Latency under concurrency:
    - Seeds 1,000 conversations with 11,000 messages.
    - Runs 100 concurrent search queries across diverse terms.
    - Measures P50, P90, P95, and P99 latencies.
    - Asserts P99 latency remains < 50ms.
    """
    search_engine = SearchEngine(challenge_db)
    now_ms = int(time.time() * 1000)

    topics = [
        ("Quantum", "Explain Shor quantum factoring", "def qft(circuit): pass"),
        ("Machine Learning", "Backpropagation through attention layer", "class Attention(nn.Module): pass"),
        ("Databases", "SQLite WAL mode concurrent reader performance", "PRAGMA journal_mode = WAL;"),
        ("Systems", "Tokio epoll runtime event loop on Linux", "async fn handle() -> io::Result<()>"),
        ("Web", "NullPointerException during React hydration", "throw new NullPointerException();"),
        ("Security", "AES-256-GCM authenticated encryption", "from cryptography.hazmat.primitives.ciphers.aead import AESGCM"),
        ("International", "Optimization of full-text search FTS5", "SELECT * FROM messages_fts;"),
    ]

    conv_rows = []
    msg_rows = []
    msg_id = 1

    for c in range(1, 1001):
        c_id = f"conv_stress_{c:04d}"
        topic, prompt, code = topics[c % len(topics)]
        conv_rows.append((c_id, f"{topic} #{c}", "gemini-2.5-pro", None, None, None, None, 0, 0, now_ms - c * 1000, now_ms, None))

        # 11 messages per conversation (1 system + 5 user/assistant turns) = 11,000 messages
        msg_rows.append((c_id, None, "system", f"System prompt for {topic}", None, "gemini-2.5-pro", None, 0, now_ms, now_ms, None))
        parent = msg_id
        msg_id += 1

        for turn in range(5):
            msg_rows.append((c_id, parent, "user", f"{prompt} turn {turn+1}", None, "gemini-2.5-pro", None, 0, now_ms + turn * 100, now_ms + turn * 100, None))
            parent = msg_id
            msg_id += 1

            msg_rows.append((c_id, parent, "assistant", f"Response: {code}", "Deep reasoning", "gemini-2.5-pro", None, 0, now_ms + turn * 100 + 50, now_ms + turn * 100 + 50, None))
            parent = msg_id
            msg_id += 1

    # Bulk insert within single transaction
    t_seed_start = time.perf_counter()
    async with challenge_db.transaction() as conn:
        await conn.executemany(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            conv_rows,
        )
        await conn.executemany(
            "INSERT INTO messages (conversation_id, parent_id, role, content, thoughts, model, token_count, is_error, created_at, updated_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            msg_rows,
        )
    t_seed_dur = time.perf_counter() - t_seed_start
    assert t_seed_dur < 5.0, f"Seeding took {t_seed_dur:.2f}s (> 5.0s limit)"

    # Verify seeding counts
    conv_cnt = await challenge_db.fetch_one("SELECT count(*) as cnt FROM conversations;")
    msg_cnt = await challenge_db.fetch_one("SELECT count(*) as cnt FROM messages;")
    assert conv_cnt["cnt"] == 1000
    assert msg_cnt["cnt"] == 11000

    benchmark_queries = [
        "quantum",
        "factoring",
        "Attention",
        "Tokio epoll",
        "NullPointerException",
        "AESGCM",
        "FTS5",
        "Optimization",
        "C++20",
        "Error: NullPointerException",
        "nonexistent_token_benchmark_xyz",
    ]

    # Warmup search
    await search_engine.search("quantum")

    latencies: List[float] = []

    async def run_single_concurrent_query(q: str) -> None:
        t0 = time.perf_counter()
        res = await search_engine.search(q, limit=20)
        dt_ms = (time.perf_counter() - t0) * 1000
        latencies.append(dt_ms)
        assert isinstance(res, list)

    # Launch 100 concurrent search queries simultaneously
    t_concurrency_start = time.perf_counter()
    tasks = [
        run_single_concurrent_query(benchmark_queries[i % len(benchmark_queries)])
        for i in range(100)
    ]
    await asyncio.gather(*tasks)
    total_concurrent_time_ms = (time.perf_counter() - t_concurrency_start) * 1000

    latencies.sort()
    min_lat = latencies[0]
    p50_lat = latencies[int(len(latencies) * 0.50)]
    p90_lat = latencies[int(len(latencies) * 0.90)]
    p95_lat = latencies[int(len(latencies) * 0.95)]
    p99_lat = latencies[int(len(latencies) * 0.99)]
    max_lat = latencies[-1]

    print(f"\n[BENCHMARK] 100 Concurrent Searches across 11,000 Messages:")
    print(f"  Total Duration: {total_concurrent_time_ms:.2f} ms")
    print(f"  Min Latency:    {min_lat:.2f} ms")
    print(f"  P50 Latency:    {p50_lat:.2f} ms")
    print(f"  P90 Latency:    {p90_lat:.2f} ms")
    print(f"  P95 Latency:    {p95_lat:.2f} ms")
    print(f"  P99 Latency:    {p99_lat:.2f} ms")
    print(f"  Max Latency:    {max_lat:.2f} ms")

    # Primary SLA assertion: P99 latency must remain < 50ms
    assert p99_lat < 50.0, (
        f"Concurrency SLA Violation: P99 latency was {p99_lat:.2f}ms (> 50.0ms limit)! "
        f"Total concurrent batch duration: {total_concurrent_time_ms:.2f}ms"
    )
