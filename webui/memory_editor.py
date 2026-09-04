from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ALLOWED_MEMORY_TYPES = {
    "small_memory",
    "preference_memory",
    "relationship_memory",
    "life_signal",
    "project_context",
    "context_summary",
}
ALLOWED_STATUSES = {"active", "stale", "deprecated"}


class MemoryEditorError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class MemoryEditor:
    """Small, validated editor for aling_memory's versioned JSON store."""

    def __init__(
        self,
        path_provider: Callable[[], Path | None],
        enabled_provider: Callable[[], bool],
        invalidate_callback: Callable[[], None] | None = None,
    ) -> None:
        self.path_provider = path_provider
        self.enabled_provider = enabled_provider
        self.invalidate_callback = invalidate_callback or (lambda: None)
        self._lock = threading.RLock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            path, root = self._load()
            scopes = self._scope_map(root)
            items: list[dict[str, Any]] = []
            for scope_ref, (scope_id, scope) in scopes.items():
                label = f"会话 {scope_ref[:8]}"
                for raw in scope.get("memories", []):
                    if isinstance(raw, dict):
                        items.append(self._public_item(raw, scope_ref, label))
            items.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
            return {
                "ok": True,
                "editable": bool(self.enabled_provider()),
                "store_found": True,
                "scope_count": len(scopes),
                "item_count": len(items),
                "scopes": [
                    {
                        "ref": ref,
                        "label": f"会话 {ref[:8]}",
                        "item_count": len(scope.get("memories", [])),
                    }
                    for ref, (_scope_id, scope) in scopes.items()
                ],
                "items": items,
                "store_updated_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
            }

    def add(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_enabled()
        with self._lock:
            _path, root = self._load()
            scope_ref, scope_id, scope = self._resolve_scope(root, payload.get("scope_ref"))
            now = self._now()
            item = {
                "id": f"mem_{secrets.token_hex(6)}",
                "type": self._memory_type(payload.get("type")),
                "content": self._content(payload.get("content")),
                "tags": self._tags(payload.get("tags")),
                "use_rule": self._short_text(payload.get("use_rule"), 500),
                "tone": self._short_text(payload.get("tone"), 200),
                "confidence": self._confidence(payload.get("confidence", 0.8)),
                "source": "dashboard_manual",
                "status": "active",
                "ttl_days": self._ttl_days(payload.get("ttl_days")),
                "created_at": now,
                "updated_at": now,
                "last_used_at": None,
                "used_count": 0,
            }
            scope.setdefault("memories", []).append(item)
            self._save(root)
            return self._public_item(item, scope_ref, f"会话 {scope_ref[:8]}")

    def update(self, memory_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_enabled()
        with self._lock:
            _path, root = self._load()
            scope_ref, _scope_id, scope = self._resolve_scope(root, payload.get("scope_ref"))
            item = self._find_item(scope, memory_id)
            if "content" in payload:
                item["content"] = self._content(payload.get("content"))
            if "type" in payload:
                item["type"] = self._memory_type(payload.get("type"))
            if "tags" in payload:
                item["tags"] = self._tags(payload.get("tags"))
            if "use_rule" in payload:
                item["use_rule"] = self._short_text(payload.get("use_rule"), 500)
            if "tone" in payload:
                item["tone"] = self._short_text(payload.get("tone"), 200)
            if "confidence" in payload:
                item["confidence"] = self._confidence(payload.get("confidence"))
            if "ttl_days" in payload:
                item["ttl_days"] = self._ttl_days(payload.get("ttl_days"))
            item["updated_at"] = self._now()
            self._save(root)
            return self._public_item(item, scope_ref, f"会话 {scope_ref[:8]}")

    def set_status(self, memory_id: str, payload: dict[str, Any], status: str) -> dict[str, Any]:
        self._require_enabled()
        if status not in ALLOWED_STATUSES:
            raise MemoryEditorError("invalid_status", "不支持的记忆状态。")
        with self._lock:
            _path, root = self._load()
            scope_ref, _scope_id, scope = self._resolve_scope(root, payload.get("scope_ref"))
            item = self._find_item(scope, memory_id)
            item["status"] = status
            item["updated_at"] = self._now()
            self._save(root)
            return self._public_item(item, scope_ref, f"会话 {scope_ref[:8]}")

    def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = self._short_text(payload.get("text"), 800)
        if not query:
            raise MemoryEditorError("empty_query", "请输入要测试的消息。")
        with self._lock:
            _path, root = self._load()
            scope_ref, _scope_id, scope = self._resolve_scope(root, payload.get("scope_ref"))
            scored: list[tuple[float, dict[str, Any], list[str]]] = []
            query_lower = query.lower()
            query_bigrams = self._bigrams(query_lower)
            for item in scope.get("memories", []):
                if not isinstance(item, dict) or item.get("status", "active") != "active" or self._expired(item):
                    continue
                reasons: list[str] = []
                score = self._confidence(item.get("confidence", 0.7))
                tags = self._tags(item.get("tags", []))
                tag_hits = [tag for tag in tags if tag.lower() in query_lower]
                if tag_hits:
                    score += min(4.0, len(tag_hits) * 2.0)
                    reasons.append(f"命中标签：{'、'.join(tag_hits[:3])}")
                haystack = " ".join(
                    str(item.get(key, "")) for key in ("content", "use_rule", "tone", "type")
                ).lower()
                shared = query_bigrams & self._bigrams(haystack)
                if shared:
                    score += min(3.0, len(shared) * 0.35)
                    reasons.append("内容关键词相近")
                if item.get("source") in {"manual", "dashboard_manual"}:
                    score += 0.25
                if not reasons:
                    continue
                scored.append((score, item, reasons))
            scored.sort(key=lambda row: row[0], reverse=True)
            matches = []
            for score, item, reasons in scored[:5]:
                public = self._public_item(item, scope_ref, f"会话 {scope_ref[:8]}")
                public["match_score"] = round(score, 2)
                public["match_reason"] = "；".join(reasons)
                matches.append(public)
            return {
                "ok": True,
                "approximate": True,
                "message": "这是 Dashboard 的快速近似预览；实际注入仍以 aling_memory 插件检索结果为准。",
                "match_count": len(matches),
                "matches": matches,
            }

    def _load(self) -> tuple[Path, dict[str, Any]]:
        path = self.path_provider()
        if path is None or not path.exists() or path.name != "memory_store.json":
            raise MemoryEditorError("store_missing", "没有找到 aling_memory 的 memory_store.json。", 503)
        try:
            root = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise MemoryEditorError("invalid_store", f"记忆文件 JSON 格式错误：{exc}", 503) from exc
        except OSError as exc:
            raise MemoryEditorError("store_unreadable", f"无法读取记忆文件：{exc}", 503) from exc
        if not isinstance(root, dict) or not isinstance(root.get("scopes", {}), dict):
            raise MemoryEditorError("invalid_store", "记忆文件结构不受支持。", 503)
        root.setdefault("version", 1)
        root.setdefault("scopes", {})
        return path, root

    def _save(self, root: dict[str, Any]) -> None:
        path = self.path_provider()
        if path is None or path.name != "memory_store.json":
            raise MemoryEditorError("store_missing", "记忆文件已不可用。", 503)
        path.parent.mkdir(parents=True, exist_ok=True)
        backup = path.with_suffix(path.suffix + ".bak")
        temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            if path.exists():
                shutil.copy2(path, backup)
            with temp.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(root, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, path)
            self.invalidate_callback()
        except OSError as exc:
            raise MemoryEditorError("store_write_failed", f"保存记忆失败：{exc}", 503) from exc
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _scope_map(self, root: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
        result: dict[str, tuple[str, dict[str, Any]]] = {}
        for scope_id, scope in root.get("scopes", {}).items():
            if not isinstance(scope, dict):
                continue
            scope.setdefault("memories", [])
            scope.setdefault("candidates", [])
            ref = hashlib.sha256(str(scope_id).encode("utf-8")).hexdigest()[:16]
            result[ref] = (str(scope_id), scope)
        return result

    def _resolve_scope(self, root: dict[str, Any], requested: Any) -> tuple[str, str, dict[str, Any]]:
        scopes = self._scope_map(root)
        ref = str(requested or "").strip()
        if not ref and len(scopes) == 1:
            ref = next(iter(scopes))
        if not ref or ref not in scopes:
            raise MemoryEditorError("scope_required", "请选择一个已有会话。")
        scope_id, scope = scopes[ref]
        return ref, scope_id, scope

    @staticmethod
    def _find_item(scope: dict[str, Any], memory_id: str) -> dict[str, Any]:
        for item in scope.get("memories", []):
            if isinstance(item, dict) and str(item.get("id")) == str(memory_id):
                return item
        raise MemoryEditorError("memory_not_found", "没有找到这条记忆。", 404)

    def _public_item(self, item: dict[str, Any], scope_ref: str, scope_label: str) -> dict[str, Any]:
        return {
            "id": str(item.get("id") or ""),
            "scope_ref": scope_ref,
            "scope_label": scope_label,
            "type": str(item.get("type") or "small_memory"),
            "content": self._short_text(item.get("content"), 1000),
            "tags": self._tags(item.get("tags", [])),
            "use_rule": self._short_text(item.get("use_rule"), 500),
            "tone": self._short_text(item.get("tone"), 200),
            "confidence": self._confidence(item.get("confidence", 0.7)),
            "source": self._short_text(item.get("source"), 80),
            "status": str(item.get("status") or "active"),
            "ttl_days": self._ttl_days(item.get("ttl_days")),
            "created_at": self._short_text(item.get("created_at"), 80),
            "updated_at": self._short_text(item.get("updated_at"), 80),
            "last_used_at": self._short_text(item.get("last_used_at"), 80),
            "used_count": max(0, int(item.get("used_count") or 0)),
            "expired": self._expired(item),
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    @staticmethod
    def _memory_type(value: Any) -> str:
        result = str(value or "small_memory").strip()
        if result not in ALLOWED_MEMORY_TYPES:
            raise MemoryEditorError("invalid_type", "不支持的记忆类型。")
        return result

    @staticmethod
    def _content(value: Any) -> str:
        result = str(value or "").strip()
        if not result:
            raise MemoryEditorError("empty_content", "记忆内容不能为空。")
        if len(result) > 1000:
            raise MemoryEditorError("content_too_long", "记忆内容不能超过 1000 个字符。")
        return result

    @staticmethod
    def _short_text(value: Any, limit: int) -> str:
        return str(value or "").strip()[:limit]

    @staticmethod
    def _tags(value: Any) -> list[str]:
        if isinstance(value, str):
            values = value.replace("，", ",").split(",")
        elif isinstance(value, list):
            values = value
        else:
            values = []
        result: list[str] = []
        for tag in values:
            clean = str(tag).strip()[:40]
            if clean and clean not in result:
                result.append(clean)
        return result[:10]

    @staticmethod
    def _confidence(value: Any) -> float:
        try:
            return round(max(0.0, min(1.0, float(value))), 2)
        except (TypeError, ValueError):
            raise MemoryEditorError("invalid_confidence", "可信度必须在 0 到 1 之间。")

    @staticmethod
    def _ttl_days(value: Any) -> int | None:
        if value in (None, "", 0, "0"):
            return None
        try:
            result = int(value)
        except (TypeError, ValueError):
            raise MemoryEditorError("invalid_ttl", "有效期必须是天数。")
        if result < 1 or result > 3650:
            raise MemoryEditorError("invalid_ttl", "有效期允许范围为 1 到 3650 天。")
        return result

    @staticmethod
    def _expired(item: dict[str, Any]) -> bool:
        ttl = item.get("ttl_days")
        if not ttl:
            return False
        try:
            created = datetime.fromisoformat(str(item.get("created_at", "")).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - created).days >= int(ttl)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _bigrams(value: str) -> set[str]:
        compact = "".join(char for char in value if not char.isspace())
        return {compact[index : index + 2] for index in range(max(0, len(compact) - 1))}

    def _require_enabled(self) -> None:
        if not self.enabled_provider():
            raise MemoryEditorError("editing_disabled", "记忆编辑功能尚未在插件配置中启用。", 403)
