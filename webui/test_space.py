from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Callable


class TestSpaceCleaner:
    """Delete only explicitly namespaced test state from continuity plugins."""

    def __init__(self, plugin_dirs_provider: Callable[[str], list[Path]]) -> None:
        self.plugin_dirs_provider = plugin_dirs_provider
        self._lock = threading.RLock()

    def clear(self) -> dict[str, int]:
        with self._lock:
            return {
                "inner_state_files": self._clear_inner_files(),
                "companion_sessions": self._clear_companion_sessions(),
            }

    def snapshot(self) -> dict[str, int | bool]:
        with self._lock:
            return {
                "ok": True,
                "inner_state_files": sum(1 for path in self._inner_state_paths() if path.is_file()),
                "companion_sessions": self._count_companion_sessions(),
            }

    def _inner_state_paths(self):
        seen: set[Path] = set()
        for base in self.plugin_dirs_provider("astrbot_plugin_inner_continuity"):
            for directory in (base / "data" / "inner_continuity", base / "inner_continuity"):
                try:
                    resolved = directory.resolve()
                except OSError:
                    continue
                if resolved in seen or not resolved.is_dir():
                    continue
                seen.add(resolved)
                yield from resolved.glob("test-account_*.json")

    def _companion_session_files(self):
        seen: set[Path] = set()
        for base in self.plugin_dirs_provider("astrbot_plugin_companion_support"):
            for path in (base / "sessions.json", base / "data" / "sessions.json"):
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                if resolved in seen or not resolved.is_file():
                    continue
                seen.add(resolved)
                yield resolved

    def _count_companion_sessions(self) -> int:
        count = 0
        for path in self._companion_session_files():
            try:
                root = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            sessions = root.get("sessions", {}) if isinstance(root, dict) else {}
            if isinstance(sessions, dict):
                count += sum(1 for key in sessions if str(key).startswith("test-account:"))
        return count

    def _clear_inner_files(self) -> int:
        removed = 0
        for path in self._inner_state_paths():
            if path.is_file():
                path.unlink()
                removed += 1
        return removed

    def _clear_companion_sessions(self) -> int:
        removed = 0
        for resolved in self._companion_session_files():
            try:
                root = json.loads(resolved.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            sessions = root.get("sessions", {}) if isinstance(root, dict) else {}
            if not isinstance(sessions, dict):
                continue
            keys = [key for key in sessions if str(key).startswith("test-account:")]
            for key in keys:
                sessions.pop(key, None)
            if keys:
                self._save_atomic(resolved, root)
                removed += len(keys)
        return removed

    @staticmethod
    def _save_atomic(path: Path, root: dict) -> None:
        backup = path.with_suffix(path.suffix + ".bak")
        temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        if path.exists():
            shutil.copy2(path, backup)
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(root, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
