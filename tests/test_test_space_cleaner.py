from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_aling_life_dashboard.webui.test_space import TestSpaceCleaner


class TestSpaceCleanerTests(unittest.TestCase):
    def test_only_namespaced_test_state_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inner_dir = root / "inner" / "data" / "inner_continuity"
            inner_dir.mkdir(parents=True)
            (inner_dir / "private_formal.json").write_text("{}", encoding="utf-8")
            (inner_dir / "test-account_test_private_test.json").write_text("{}", encoding="utf-8")

            companion_dir = root / "companion"
            companion_dir.mkdir()
            sessions_path = companion_dir / "sessions.json"
            sessions_path.write_text(
                json.dumps({"schema_version": 1, "sessions": {"formalhash": {}, "test-account:testhash": {}}}),
                encoding="utf-8",
            )

            directories = {
                "astrbot_plugin_inner_continuity": [root / "inner"],
                "astrbot_plugin_companion_support": [companion_dir],
            }
            cleaner = TestSpaceCleaner(lambda name: directories.get(name, []))
            self.assertEqual(cleaner.snapshot(), {"ok": True, "inner_state_files": 1, "companion_sessions": 1})
            result = cleaner.clear()

            self.assertEqual(result, {"inner_state_files": 1, "companion_sessions": 1})
            self.assertTrue((inner_dir / "private_formal.json").exists())
            self.assertFalse((inner_dir / "test-account_test_private_test.json").exists())
            sessions = json.loads(sessions_path.read_text(encoding="utf-8"))["sessions"]
            self.assertEqual(set(sessions), {"formalhash"})


if __name__ == "__main__":
    unittest.main()
