# tests/e2e/tier2_boundaries/test_tier2_persistence_branching.py
"""Tier 2: Boundary & Corner Cases for Features F23 to F27 (Storage, FTS5, Branching, Export)."""

import unittest
import sqlite3
import json
from tests.e2e.harness import (
    StorageEngine,
    FTS5SearchEngine,
    ConversationTree,
    ThreadExporter,
    RemoteSyncEngine,
)

class TestTier2PersistenceBranching(unittest.TestCase):
    """Boundary & Corner Cases for F23 to F27."""

    def setUp(self):
        self.storage = StorageEngine(":memory:")
        self.fts = FTS5SearchEngine(self.storage.conn)

    def tearDown(self):
        self.storage.close()

    # --- F23: SQLite Persistence Schema ---
    def test_t2_f23_01_insert_duplicate_conversation_id_fails(self):
        self.storage.create_conversation("c_dup", "Title 1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.storage.create_conversation("c_dup", "Title 2")

    def test_t2_f23_02_insert_message_invalid_foreign_key_fails(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.storage.add_message("m_orphan", "non_existent_conv", "user", "Hello")

    def test_t2_f23_03_message_content_one_million_chars(self):
        self.storage.create_conversation("c_big", "Big Chat")
        huge_text = "M" * 1000000
        self.storage.add_message("m_huge", "c_big", "user", huge_text)
        msgs = self.storage.get_messages("c_big")
        self.assertEqual(len(msgs[0]["content"]), 1000000)

    def test_t2_f23_04_conversation_empty_title_allowed(self):
        conv_id = self.storage.create_conversation("c_empty_t", "")
        conv = self.storage.get_conversation("c_empty_t")
        self.assertEqual(conv["title"], "")

    def test_t2_f23_05_transaction_rollback_preserves_state(self):
        self.storage.create_conversation("c_trans", "Initial")
        try:
            cur = self.storage.conn.cursor()
            cur.execute("UPDATE conversations SET title = 'Changed' WHERE id = 'c_trans'")
            raise RuntimeError("Simulated failure")
        except RuntimeError:
            self.storage.conn.rollback()

        conv = self.storage.get_conversation("c_trans")
        self.assertEqual(conv["title"], "Initial")

    # --- F24: SQLite FTS5 Search ---
    def test_t2_f24_01_fts5_empty_query_returns_zero(self):
        res, _ = self.fts.search("")
        self.assertEqual(len(res), 0)

    def test_t2_f24_02_fts5_punctuation_only_returns_zero(self):
        res, _ = self.fts.search("!@#$%^&*()_+=-")
        self.assertEqual(len(res), 0)

    def test_t2_f24_03_fts5_unmatched_quotes_sanitized(self):
        self.storage.create_conversation("c_quote", "Quote Chat")
        self.storage.add_message("m_q", "c_quote", "user", "Special term in quotes")
        res, _ = self.fts.search('"Special term')
        self.assertEqual(len(res), 1)

    def test_t2_f24_04_fts5_boolean_operators_without_args(self):
        res, _ = self.fts.search("AND OR NOT")
        self.assertEqual(len(res), 0)

    def test_t2_f24_05_fts5_diacritics_unicode_matching(self):
        self.storage.create_conversation("c_diacritic", "Diacritic Chat")
        self.storage.add_message("m_d", "c_diacritic", "user", "Un café délicieux à Paris")
        # Tokenizer unicode61 remove_diacritics 2 matches 'cafe' to 'café'
        res, _ = self.fts.search("cafe")
        self.assertEqual(len(res), 1)

    # --- F25: Remote Conversation Sync ---
    def test_t2_f25_01_remote_sync_empty_list_zero_inserts(self):
        sync = RemoteSyncEngine(self.storage)
        count = sync.sync_remote_conversations([])
        self.assertEqual(count, 0)

    def test_t2_f25_02_remote_sync_missing_title_defaults(self):
        sync = RemoteSyncEngine(self.storage)
        count = sync.sync_remote_conversations([{"cid": "rem_no_title"}])
        self.assertEqual(count, 1)
        row = self.storage.get_conversation("local_rem_no_title")
        self.assertEqual(row["title"], "Synced Chat")

    def test_t2_f25_03_remote_sync_duplicate_cids_in_batch(self):
        sync = RemoteSyncEngine(self.storage)
        batch = [{"cid": "same_cid"}, {"cid": "same_cid"}]
        count = sync.sync_remote_conversations(batch)
        self.assertEqual(count, 1)

    def test_t2_f25_04_remote_sync_preserves_messages(self):
        self.storage.create_conversation("local_rem_existing", "Pre-existing", remote_id="rem_existing")
        self.storage.add_message("m_keep", "local_rem_existing", "user", "Preserved content")
        sync = RemoteSyncEngine(self.storage)
        sync.sync_remote_conversations([{"cid": "rem_existing", "title": "Updated Remote"}])
        msgs = self.storage.get_messages("local_rem_existing")
        self.assertEqual(len(msgs), 1)

    def test_t2_f25_05_remote_sync_hundred_conversations(self):
        sync = RemoteSyncEngine(self.storage)
        batch = [{"cid": f"rem_bulk_{i}", "title": f"Bulk {i}"} for i in range(100)]
        count = sync.sync_remote_conversations(batch)
        self.assertEqual(count, 100)

    # --- F26: DAG Conversation Branching ---
    def test_t2_f26_01_linear_thread_non_existent_leaf(self):
        tree = ConversationTree()
        thread = tree.get_linear_thread("non_existent_leaf")
        self.assertEqual(thread, [])

    def test_t2_f26_02_dag_chain_500_messages(self):
        tree = ConversationTree()
        prev = None
        for i in range(500):
            mid = f"m_{i}"
            tree.add_message(mid, prev, "user", f"Turn {i}")
            prev = mid
        thread = tree.get_linear_thread("m_499")
        self.assertEqual(len(thread), 500)

    def test_t2_f26_03_self_referential_parent_handled(self):
        tree = ConversationTree()
        tree.add_message("m_self", "m_self", "user", "Loop")
        thread = tree.get_linear_thread("m_self")
        self.assertEqual(len(thread), 1)

    def test_t2_f26_04_twenty_branching_levels(self):
        tree = ConversationTree()
        prev = None
        for i in range(20):
            mid = f"lvl_{i}"
            tree.add_message(mid, prev, "assistant", f"Level {i}")
            prev = mid
        thread = tree.get_linear_thread("lvl_19")
        self.assertEqual(len(thread), 20)

    def test_t2_f26_05_linear_thread_preserves_role_and_model(self):
        tree = ConversationTree()
        tree.add_message("u1", None, "user", "Q", model="gemini-2.5-pro")
        tree.add_message("a1", "u1", "assistant", "A", model="gemini-2.5-pro")
        thread = tree.get_linear_thread("a1")
        self.assertEqual(thread[0]["model"], "gemini-2.5-pro")

    # --- F27: Thread Export Pipeline ---
    def test_t2_f27_01_export_markdown_empty_messages(self):
        conv = {"id": "c_empty", "title": "Empty Thread"}
        md = ThreadExporter.export_markdown(conv, [])
        self.assertIn("# Empty Thread", md)

    def test_t2_f27_02_export_markdown_special_chars_in_title(self):
        conv = {"id": "c_special", "title": 'Discussion: "Quotes" & Special #1'}
        md = ThreadExporter.export_markdown(conv, [])
        self.assertIn("Discussion:", md)

    def test_t2_f27_03_export_json_empty_messages(self):
        conv = {"id": "c_json_empty", "title": "Empty"}
        raw = ThreadExporter.export_json(conv, [])
        parsed = json.loads(raw)
        self.assertEqual(parsed["messages"], [])

    def test_t2_f27_04_export_json_unicode_emojis(self):
        conv = {"id": "c_emoji", "title": "Emoji Chat 🚀"}
        msgs = [{"id": "m1", "role": "user", "content": "Rocket 🚀 launch ✨"}]
        raw = ThreadExporter.export_json(conv, msgs)
        parsed = json.loads(raw)
        self.assertIn("🚀", parsed["messages"][0]["content"])

    def test_t2_f27_05_export_markdown_multiple_thoughts(self):
        conv = {"id": "c_multi_thought", "title": "Multi Thoughts"}
        msgs = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1", "thinking_content": "Thought 1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2", "thinking_content": "Thought 2"},
        ]
        md = ThreadExporter.export_markdown(conv, msgs)
        self.assertEqual(md.count("<summary>Thought Process</summary>"), 2)


if __name__ == "__main__":
    unittest.main()
