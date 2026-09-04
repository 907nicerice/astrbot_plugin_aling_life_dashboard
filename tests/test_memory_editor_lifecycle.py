from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from astrbot_plugin_aling_life_dashboard.webui.memory_editor import MemoryEditor


class MemoryEditorLifecycleTests(unittest.TestCase):
    def test_ttl_edit_restarts_expiration_and_keeps_lifecycle_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory_store.json"
            path.write_text(
                json.dumps({"version": 1, "scopes": {"private:user": {"memories": [], "candidates": []}}}),
                encoding="utf-8",
            )
            editor = MemoryEditor(lambda: path, lambda: True)
            scope_ref = editor.snapshot()["scopes"][0]["ref"]
            item = editor.add(
                {
                    "scope_ref": scope_ref,
                    "type": "preference_memory",
                    "content": "用户喜欢自然的回复",
                    "ttl_days": 30,
                    "importance": 0.85,
                    "stability": 0.9,
                    "sensitivity": "low",
                }
            )
            self.assertEqual(item["importance"], 0.85)
            self.assertEqual(item["stability"], 0.9)
            first_expiry = datetime.fromisoformat(item["expires_at"])
            self.assertGreater(first_expiry, datetime.now(timezone.utc) + timedelta(days=29))

            updated = editor.update(item["id"], {"scope_ref": scope_ref, "ttl_days": 60})
            second_expiry = datetime.fromisoformat(updated["expires_at"])
            self.assertGreater(second_expiry, first_expiry + timedelta(days=29))

    def test_candidate_can_be_approved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory_store.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "scopes": {
                            "private:user": {
                                "memories": [],
                                "candidates": [
                                    {
                                        "id": "cand_one",
                                        "suggested_type": "preference_memory",
                                        "content": "用户偏好自然简短的回答",
                                        "reason": "重复表达",
                                        "confidence": 0.86,
                                        "importance": 0.8,
                                        "stability": 0.9,
                                        "sensitivity": "low",
                                        "ttl_days": None,
                                        "evidence_count": 2,
                                    }
                                ],
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            editor = MemoryEditor(lambda: path, lambda: True)
            snapshot = editor.snapshot()
            scope_ref = snapshot["scopes"][0]["ref"]
            self.assertEqual(snapshot["candidate_count"], 1)
            item = editor.approve_candidate("cand_one", {"scope_ref": scope_ref})
            self.assertEqual(item["source"], "dashboard_approved")
            self.assertEqual(item["evidence_count"], 2)
            self.assertEqual(editor.snapshot()["candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
