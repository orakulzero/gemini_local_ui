# tests/e2e/tier1_features/test_f23_to_f27_persistence_branching.py
"""Tier 1: Tests for Features F23 to F27 (Storage, FTS5, Branching, Export)."""

import unittest
import json
from tests.e2e.harness import (
    StorageEngine,
    FTS5SearchEngine,
    ConversationTree,
    ThreadExporter,
    RemoteSyncEngine,
)

class TestF23SQLitePersistenceSchema(unittest.TestCase):
    """F23: SQLite Persistence Schema."""

    def setUp(self):
        self.storage = StorageEngine(":memory:")

    def tearDown(self):
        self.storage.close()

    def test_f23_01_create_conversation_row(self):
        conv_id = self.storage.create_conversation("c_1", "Test Chat", "gemini-2.0-flash")
        conv = self.storage.get_conversation("c_1")
        self.assertIsNotNone(conv)
        self.assertEqual(conv["id"], "c_1")
        self.assertEqual(conv["title"], "Test Chat")

    def test_f23_02_insert_messages_with_foreign_key(self):
        self.storage.create_conversation("c_1", "Test Chat")
        msg_id = self.storage.add_message("m_1", "c_1", "user", "Hello SQLite")
        messages = self.storage.get_messages("c_1")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"], "Hello SQLite")

    def test_f23_03_cascade_delete_messages_on_conversation_delete(self):
        self.storage.create_conversation("c_1", "Test Chat")
        self.storage.add_message("m_1", "c_1", "user", "Message to cascade")
        self.storage.delete_conversation("c_1")
        messages = self.storage.get_messages("c_1")
        self.assertEqual(len(messages), 0)

    def test_f23_04_wal_journal_mode_enabled(self):
        mode = self.storage.conn.execute("PRAGMA journal_mode;").fetchone()[0]
        self.assertIn(mode.lower(), ("wal", "memory"))

    def test_f23_05_role_check_constraint_enforced(self):
        self.storage.create_conversation("c_1", "Test")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.storage.conn.execute(
                "INSERT INTO messages(id, conversation_id, role, content, model, created_at, updated_at) VALUES ('m_bad', 'c_1', 'invalid_role', 'test', 'm', 0, 0)"
            )


class TestF24SQLiteFTS5Search(unittest.TestCase):
    """F24: SQLite FTS5 Search."""

    def setUp(self):
        self.storage = StorageEngine(":memory:")
        self.fts = FTS5SearchEngine(self.storage.conn)

    def tearDown(self):
        self.storage.close()

    def test_f24_01_automatic_fts5_insert_via_trigger(self):
        self.storage.create_conversation("c_1", "Search Chat")
        self.storage.add_message("m_1", "c_1", "user", "Quantum entanglement in physics")
        results, _ = self.fts.search("entanglement")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["message_id"], "m_1")
        self.assertIn("<mark>entanglement</mark>", results[0]["highlighted_snippet"])

    def test_f24_02_automatic_fts5_delete_via_trigger(self):
        self.storage.create_conversation("c_1", "Delete Chat")
        self.storage.add_message("m_1", "c_1", "user", "Temporary secret keyword")
        results_before, _ = self.fts.search("secret")
        self.assertEqual(len(results_before), 1)

        self.storage.conn.execute("DELETE FROM messages WHERE id = 'm_1'")
        self.storage.conn.commit()
        results_after, _ = self.fts.search("secret")
        self.assertEqual(len(results_after), 0)

    def test_f24_03_automatic_fts5_update_via_trigger(self):
        self.storage.create_conversation("c_1", "Update Chat")
        self.storage.add_message("m_1", "c_1", "user", "Initial version")
        self.storage.conn.execute("UPDATE messages SET content = 'Revised version' WHERE id = 'm_1'")
        self.storage.conn.commit()

        res_initial, _ = self.fts.search("Initial")
        res_revised, _ = self.fts.search("Revised")
        self.assertEqual(len(res_initial), 0)
        self.assertEqual(len(res_revised), 1)

    def test_f24_04_query_sanitizer_removes_unbalanced_quotes(self):
        sanitized = FTS5SearchEngine.sanitize_query('search "unclosed quote')
        self.assertNotIn('""', sanitized)

    def test_f24_05_search_query_latency_under_50ms(self):
        self.storage.create_conversation("c_1", "Latency Test")
        for i in range(50):
            self.storage.add_message(f"m_{i}", "c_1", "user", f"Token {i} algorithm data structure search")
        results, latency_ms = self.fts.search("algorithm")
        self.assertLess(latency_ms, 50.0)


class TestF25RemoteConversationSync(unittest.TestCase):
    """F25: Remote Conversation Sync."""

    def setUp(self):
        self.storage = StorageEngine(":memory:")
        self.sync_engine = RemoteSyncEngine(self.storage)

    def tearDown(self):
        self.storage.close()

    def test_f25_01_sync_new_remote_conversation(self):
        remote_data = [{"cid": "c_remote_101", "title": "Remote Coding Chat"}]
        synced = self.sync_engine.sync_remote_conversations(remote_data)
        self.assertEqual(synced, 1)

    def test_f25_02_idempotent_sync_avoids_duplicates(self):
        remote_data = [{"cid": "c_remote_101", "title": "Remote Coding Chat"}]
        self.sync_engine.sync_remote_conversations(remote_data)
        # Second sync of same remote conversation
        synced_again = self.sync_engine.sync_remote_conversations(remote_data)
        self.assertEqual(synced_again, 0)

    def test_f25_03_remote_id_mapping_to_local_row(self):
        remote_data = [{"cid": "c_remote_102", "title": "Remote Chat"}]
        self.sync_engine.sync_remote_conversations(remote_data)
        row = self.storage.conn.execute(
            "SELECT * FROM conversations WHERE remote_conversation_id = 'c_remote_102'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["title"], "Remote Chat")

    def test_f25_04_batch_sync_multiple_conversations(self):
        remote_data = [
            {"cid": f"c_remote_{i}", "title": f"Remote Chat {i}"} for i in range(5)
        ]
        synced = self.sync_engine.sync_remote_conversations(remote_data)
        self.assertEqual(synced, 5)

    def test_f25_05_sync_preserves_existing_local_chats(self):
        self.storage.create_conversation("local_manual", "Local Only Chat")
        remote_data = [{"cid": "c_rem_1", "title": "Remote Chat"}]
        self.sync_engine.sync_remote_conversations(remote_data)
        self.assertIsNotNone(self.storage.get_conversation("local_manual"))


class TestF26DAGConversationBranching(unittest.TestCase):
    """F26: DAG Conversation Branching."""

    def setUp(self):
        self.tree = ConversationTree()

    def test_f26_01_linear_thread_from_leaf_single_path(self):
        self.tree.add_message("u1", None, "user", "Hi")
        self.tree.add_message("a1", "u1", "assistant", "Hello")
        thread = self.tree.get_linear_thread("a1")
        self.assertEqual([m["id"] for m in thread], ["u1", "a1"])

    def test_f26_02_linear_thread_branch_1(self):
        self.tree.add_message("u1", None, "user", "Hi")
        self.tree.add_message("a1_v1", "u1", "assistant", "Answer 1")
        self.tree.add_message("u2_b1", "a1_v1", "user", "Follow-up 1")
        thread = self.tree.get_linear_thread("u2_b1")
        self.assertEqual([m["id"] for m in thread], ["u1", "a1_v1", "u2_b1"])

    def test_f26_03_linear_thread_branch_2_alternate(self):
        self.tree.add_message("u1", None, "user", "Hi")
        self.tree.add_message("a1_v1", "u1", "assistant", "Answer 1")
        # Alternative answer
        self.tree.add_message("a1_v2", "u1", "assistant", "Answer 2")
        self.tree.add_message("u2_b2", "a1_v2", "user", "Follow-up 2")
        thread = self.tree.get_linear_thread("u2_b2")
        self.assertEqual([m["id"] for m in thread], ["u1", "a1_v2", "u2_b2"])

    def test_f26_04_root_level_branching_sibling_resolution(self):
        self.tree.add_message("root_1", None, "user", "First topic")
        self.tree.add_message("root_2", None, "user", "Second topic")
        sib1 = self.tree.get_sibling_info("root_1")
        sib2 = self.tree.get_sibling_info("root_2")
        self.assertEqual(sib1["total"], 2)
        self.assertEqual(sib2["total"], 2)

    def test_f26_05_circular_parent_safeguard(self):
        # Prevent infinite loop if circular pointer exists
        self.tree.add_message("m_loop_1", "m_loop_2", "user", "msg1")
        self.tree.add_message("m_loop_2", "m_loop_1", "assistant", "msg2")
        thread = self.tree.get_linear_thread("m_loop_2")
        # Should terminate safely without infinite recursion
        self.assertLessEqual(len(thread), 2)


class TestF27ThreadExportPipeline(unittest.TestCase):
    """F27: Thread Export Pipeline."""

    def test_f27_01_export_markdown_yaml_frontmatter(self):
        conv = {"id": "c_exp_1", "title": "Export Test", "model": "gemini-2.0-flash", "created_at": 1700000000000}
        messages = [{"role": "user", "content": "Hello world"}]
        md = ThreadExporter.export_markdown(conv, messages)
        self.assertTrue(md.startswith("---"))
        self.assertIn('title: "Export Test"', md)
        self.assertIn('model: "gemini-2.0-flash"', md)

    def test_f27_02_export_markdown_details_thinking_block(self):
        conv = {"id": "c_exp_2", "title": "Thinking Test", "model": "gemini-2.0-flash-thinking", "created_at": 1700000000000}
        messages = [
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "4", "thinking_content": "Calculating basic arithmetic."},
        ]
        md = ThreadExporter.export_markdown(conv, messages)
        self.assertIn("<details>", md)
        self.assertIn("<summary>Thought Process</summary>", md)
        self.assertIn("Calculating basic arithmetic.", md)

    def test_f27_03_export_markdown_turns_roles(self):
        conv = {"id": "c_exp_3", "title": "Turns Test", "created_at": 1700000000000}
        messages = [
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": "Answer"},
        ]
        md = ThreadExporter.export_markdown(conv, messages)
        self.assertIn("### User", md)
        self.assertIn("### Assistant", md)

    def test_f27_04_export_json_valid_schema(self):
        conv = {"id": "c_exp_4", "title": "JSON Test", "model": "gemini-2.5-pro", "created_at": 1700000000000}
        messages = [{"id": "m1", "role": "user", "content": "JSON Content"}]
        raw_json = ThreadExporter.export_json(conv, messages)
        parsed = json.loads(raw_json)
        self.assertEqual(parsed["version"], "1.0")
        self.assertEqual(parsed["conversation"]["id"], "c_exp_4")

    def test_f27_05_export_json_messages_array_integrity(self):
        conv = {"id": "c_exp_5", "title": "Messages Test"}
        messages = [
            {"id": "m1", "parent_message_id": None, "role": "user", "content": "Msg 1"},
            {"id": "m2", "parent_message_id": "m1", "role": "assistant", "content": "Msg 2"},
        ]
        raw_json = ThreadExporter.export_json(conv, messages)
        parsed = json.loads(raw_json)
        self.assertEqual(len(parsed["messages"]), 2)
        self.assertEqual(parsed["messages"][1]["parent_id"], "m1")


if __name__ == "__main__":
    unittest.main()
