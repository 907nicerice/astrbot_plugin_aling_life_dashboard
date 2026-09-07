from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import threading
from datetime import datetime, timedelta, timezone
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
            candidates: list[dict[str, Any]] = []
            for scope_ref, (scope_id, scope) in scopes.items():
                label = self._scope_label(scope_id, scope_ref)
                for raw in scope.get("memories", []):
                    if isinstance(raw, dict):
                        items.append(self._public_item(raw, scope_ref, label))
                for raw in scope.get("candidates", []):
                    if isinstance(raw, dict):
                        candidates.append(self._public_candidate(raw, scope_ref, label))
            items.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
            candidates.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
            return {
                "ok": True,
                "editable": bool(self.enabled_provider()),
                "store_found": True,
                "scope_count": len(scopes),
                "item_count": len(items),
                "candidate_count": len(candidates),
                "scopes": [
                    {
                        "ref": ref,
                        "label": self._scope_label(scope_id, ref),
                        "is_test_account": self._is_test_scope(scope_id),
                        "item_count": len(scope.get("memories", [])),
                    }
                    for ref, (scope_id, scope) in scopes.items()
                ],
                "items": items,
                "candidates": candidates,
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
                "importance": self._score(payload.get("importance", 0.6), "重要性"),
                "stability": self._score(payload.get("stability", 0.6), "稳定性"),
                "sensitivity": self._sensitivity(payload.get("sensitivity", "low")),
                "source": "dashboard_manual",
                "status": "active",
                "ttl_days": self._ttl_days(payload.get("ttl_days")),
                "expires_at": None,
                "created_at": now,
                "updated_at": now,
                "last_used_at": None,
                "used_count": 0,
                "evidence_count": 1,
                "last_confirmed_at": now,
                "supersedes_id": None,
            }
            item["expires_at"] = self._expires_after_days(item["ttl_days"])
            scope.setdefault("memories", []).append(item)
            self._save(root)
            return self._public_item(item, scope_ref, self._scope_label(scope_id, scope_ref))

    def update(self, memory_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_enabled()
        with self._lock:
            _path, root = self._load()
            scope_ref, scope_id, scope = self._resolve_scope(root, payload.get("scope_ref"))
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
            if "importance" in payload:
                item["importance"] = self._score(payload.get("importance"), "重要性")
            if "stability" in payload:
                item["stability"] = self._score(payload.get("stability"), "稳定性")
            if "sensitivity" in payload:
                item["sensitivity"] = self._sensitivity(payload.get("sensitivity"))
            if "ttl_days" in payload:
                item["ttl_days"] = self._ttl_days(payload.get("ttl_days"))
                item["expires_at"] = self._expires_after_days(item["ttl_days"])
            item["updated_at"] = self._now()
            self._save(root)
            return self._public_item(item, scope_ref, self._scope_label(scope_id, scope_ref))

    def set_status(self, memory_id: str, payload: dict[str, Any], status: str) -> dict[str, Any]:
        self._require_enabled()
        if status not in ALLOWED_STATUSES:
            raise MemoryEditorError("invalid_status", "不支持的记忆状态。")
        with self._lock:
            _path, root = self._load()
            scope_ref, scope_id, scope = self._resolve_scope(root, payload.get("scope_ref"))
            item = self._find_item(scope, memory_id)
            item["status"] = status
            item["updated_at"] = self._now()
            self._save(root)
            return self._public_item(item, scope_ref, self._scope_label(scope_id, scope_ref))

    def approve_candidate(self, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_enabled()
        with self._lock:
            _path, root = self._load()
            scope_ref, scope_id, scope = self._resolve_scope(root, payload.get("scope_ref"))
            candidate = self._find_candidate(scope, candidate_id)
            if self._sensitivity(candidate.get("sensitivity", "low")) == "high":
                raise MemoryEditorError("sensitive_candidate", "高敏感候选不能转为长期记忆，请直接拒绝。")
            now = self._now()
            ttl_days = self._ttl_days(candidate.get("ttl_days"))
            item = {
                "id": f"mem_{secrets.token_hex(6)}",
                "type": self._memory_type(candidate.get("suggested_type")),
                "content": self._content(candidate.get("content")),
                "tags": self._tags(candidate.get("tags")),
                "use_rule": self._short_text(candidate.get("use_rule"), 500),
                "tone": "",
                "confidence": self._confidence(candidate.get("confidence", 0.7)),
                "importance": self._score(candidate.get("importance", 0.5), "重要性"),
                "stability": self._score(candidate.get("stability", 0.5), "稳定性"),
                "sensitivity": self._sensitivity(candidate.get("sensitivity", "low")),
                "source": "dashboard_approved",
                "status": "active",
                "ttl_days": ttl_days,
                "expires_at": self._expires_after_days(ttl_days),
                "created_at": now,
                "updated_at": now,
                "last_confirmed_at": now,
                "last_used_at": None,
                "used_count": 0,
                "evidence_count": max(1, int(candidate.get("evidence_count") or 1)),
                "supersedes_id": None,
            }
            scope.setdefault("memories", []).append(item)
            scope["candidates"] = [row for row in scope.get("candidates", []) if row is not candidate]
            self._save(root)
            return self._public_item(item, scope_ref, self._scope_label(scope_id, scope_ref))

    def reject_candidate(self, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_enabled()
        with self._lock:
            _path, root = self._load()
            scope_ref, scope_id, scope = self._resolve_scope(root, payload.get("scope_ref"))
            candidate = self._find_candidate(scope, candidate_id)
            public = self._public_candidate(candidate, scope_ref, self._scope_label(scope_id, scope_ref))
            scope["candidates"] = [row for row in scope.get("candidates", []) if row is not candidate]
            self._save(root)
            return public

    def clear_test_scopes(self) -> dict[str, int]:
        self._require_enabled()
        with self._lock:
            _path, root = self._load()
            scopes = root.get("scopes", {})
            test_scope_ids = [scope_id for scope_id in scopes if self._is_test_scope(str(scope_id))]
            memory_count = 0
            candidate_count = 0
            for scope_id in test_scope_ids:
                scope = scopes.get(scope_id, {})
                if isinstance(scope, dict):
                    memory_count += len(scope.get("memories", []))
                    candidate_count += len(scope.get("candidates", []))
                scopes.pop(scope_id, None)
            if test_scope_ids:
                self._save(root)
            related_scope_count = 0
            memory_dir = _path.parent
            for filename, container_key in (
                ("context_summaries.json", "scopes"),
                ("flashback_state.json", "scopes"),
                ("user_life_mirror.json", "scopes"),
                ("recent_trace.json", "sessions"),
            ):
                related_scope_count += self._clear_test_container(memory_dir / filename, container_key)
            return {
                "scope_count": len(test_scope_ids),
                "memory_count": memory_count,
                "candidate_count": candidate_count,
                "related_scope_count": related_scope_count,
            }

    def _clear_test_container(self, path: Path, container_key: str) -> int:
        if not path.exists():
            return 0
        try:
            root = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return 0
        container = root.get(container_key, {}) if isinstance(root, dict) else {}
        if not isinstance(container, dict):
            return 0
        keys = [key for key in container if self._is_test_scope(str(key))]
        for key in keys:
            container.pop(key, None)
        if keys:
            self._save_path(path, root)
        return len(keys)

    def preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = self._short_text(payload.get("text"), 800)
        if not query:
            raise MemoryEditorError("empty_query", "请输入要测试的消息。")
        with self._lock:
            _path, root = self._load()
            scope_ref, scope_id, scope = self._resolve_scope(root, payload.get("scope_ref"))
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
                public = self._public_item(item, scope_ref, self._scope_label(scope_id, scope_ref))
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
        root["version"] = max(2, int(root.get("version") or 1))
        root.setdefault("scopes", {})
        return path, root

    def _save(self, root: dict[str, Any]) -> None:
        path = self.path_provider()
        if path is None or path.name != "memory_store.json":
            raise MemoryEditorError("store_missing", "记忆文件已不可用。", 503)
        self._save_path(path, root)

    def _save_path(self, path: Path, root: dict[str, Any]) -> None:
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

    @staticmethod
    def _is_test_scope(scope_id: str) -> bool:
        return str(scope_id).startswith("test-account:")

    @classmethod
    def _scope_label(cls, scope_id: str, scope_ref: str) -> str:
        prefix = "测试空间" if cls._is_test_scope(scope_id) else "会话"
        return f"{prefix} {scope_ref[:8]}"

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

    @staticmethod
    def _find_candidate(scope: dict[str, Any], candidate_id: str) -> dict[str, Any]:
        for item in scope.get("candidates", []):
            if isinstance(item, dict) and str(item.get("id")) == str(candidate_id):
                return item
        raise MemoryEditorError("candidate_not_found", "没有找到这条候选记忆。", 404)

    def _public_candidate(self, item: dict[str, Any], scope_ref: str, scope_label: str) -> dict[str, Any]:
        return {
            "id": str(item.get("id") or ""),
            "scope_ref": scope_ref,
            "scope_label": scope_label,
            "suggested_type": str(item.get("suggested_type") or "small_memory"),
            "content": self._short_text(item.get("content"), 1000),
            "reason": self._short_text(item.get("reason"), 500),
            "confidence": self._confidence(item.get("confidence", 0.7)),
            "importance": self._score(item.get("importance", 0.5), "重要性"),
            "stability": self._score(item.get("stability", 0.5), "稳定性"),
            "sensitivity": self._sensitivity(item.get("sensitivity", "low")),
            "decision": self._short_text(item.get("decision"), 40) or "candidate",
            "ttl_days": self._ttl_days(item.get("ttl_days")),
            "tags": self._tags(item.get("tags", [])),
            "use_rule": self._short_text(item.get("use_rule"), 500),
            "created_at": self._short_text(item.get("created_at"), 80),
            "updated_at": self._short_text(item.get("updated_at"), 80),
            "evidence_count": max(1, int(item.get("evidence_count") or 1)),
        }

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
            "importance": self._score(item.get("importance", 0.5), "重要性"),
            "stability": self._score(item.get("stability", 0.5), "稳定性"),
            "sensitivity": self._sensitivity(item.get("sensitivity", "low")),
            "source": self._short_text(item.get("source"), 80),
            "status": str(item.get("status") or "active"),
            "ttl_days": self._ttl_days(item.get("ttl_days")),
            "expires_at": self._short_text(item.get("expires_at"), 80),
            "created_at": self._short_text(item.get("created_at"), 80),
            "updated_at": self._short_text(item.get("updated_at"), 80),
            "last_used_at": self._short_text(item.get("last_used_at"), 80),
            "used_count": max(0, int(item.get("used_count") or 0)),
            "evidence_count": max(1, int(item.get("evidence_count") or 1)),
            "last_confirmed_at": self._short_text(item.get("last_confirmed_at"), 80),
            "supersedes_id": self._short_text(item.get("supersedes_id"), 80),
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
    def _score(value: Any, label: str) -> float:
        try:
            return round(max(0.0, min(1.0, float(value))), 2)
        except (TypeError, ValueError):
            raise MemoryEditorError("invalid_score", f"{label}必须在 0 到 1 之间。")

    @staticmethod
    def _sensitivity(value: Any) -> str:
        result = str(value or "low").strip().lower()
        if result not in {"low", "medium", "high"}:
            raise MemoryEditorError("invalid_sensitivity", "敏感等级不受支持。")
        return result

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
    def _expires_after_days(days: int | None) -> str | None:
        if not days:
            return None
        return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).isoformat()

    @staticmethod
    def _expired(item: dict[str, Any]) -> bool:
        explicit = item.get("expires_at")
        if explicit:
            try:
                expires = datetime.fromisoformat(str(explicit).replace("Z", "+00:00"))
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
                return datetime.now(timezone.utc) >= expires
            except (TypeError, ValueError):
                pass
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
