from __future__ import annotations

import contextlib
import json
import math
import re
import time
from datetime import datetime, time as dt_time, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except ImportError:  # pragma: no cover
    def get_astrbot_data_path() -> str:
        raise RuntimeError("AstrBot runtime unavailable")


SHARED_PLUGIN_NAME = "astrbot_plugin_shared_life_context"
BRIDGE_PLUGIN_NAME = "astrbot_plugin_qzone_life_bridge"
INNER_CONTINUITY_PLUGIN_NAME = "astrbot_plugin_inner_continuity"
ALING_MEMORY_PLUGIN_NAME = "astrbot_plugin_aling_memory"
QZONE_PLUGIN_NAMES = ("astrbot_plugin_qzone_auto_like", "qzone_auto_like", "astrbot_qzone_auto_like")
SENSITIVE_KEY_PATTERN = re.compile(
    r"(cookie|p_skey|skey|pt4_token|access_token|refresh_token|authorization|dashboard_password|password|token|secret|api_key|secret_key|private_key|unified_msg_origin)",
    re.IGNORECASE,
)
SENSITIVE_PUBLIC_KEYS = {
    "cookie_len",
    "cookie_configured",
    "cookie_has_p_skey",
    "fallback_cookie_configured",
    "has_p_skey",
}

DEFAULT_PERIOD_REFRESH_TIMES = {
    "morning": "08:30",
    "afternoon": "14:00",
    "evening": "20:00",
    "night": "00:30",
}
PERIOD_LABELS = {
    "morning": ("morning", "上午", "早上", "清晨"),
    "afternoon": ("afternoon", "下午", "中午"),
    "evening": ("evening", "晚上", "傍晚"),
    "night": ("night", "深夜", "夜里", "凌晨"),
}
DEFAULT_WINDOWS = [
    {"name": "noon", "start": "11:30", "end": "13:40", "probability": 0.18},
    {"name": "evening", "start": "18:30", "end": "23:20", "probability": 0.42},
    {"name": "late_night", "start": "23:20", "end": "00:40", "probability": 0.12},
]


class DashboardDataReader:
    def __init__(self, plugin_dir: Path, context: Any = None, dashboard_config_provider=None):
        self.plugin_dir = Path(plugin_dir)
        self.context = context
        self.dashboard_config_provider = dashboard_config_provider or (lambda: {})
        self.data_root = self._resolve_data_root()
        self._cache: tuple[float, dict[str, Any]] | None = None

    def read_all(self) -> dict[str, Any]:
        settings = self.dashboard_config_provider()
        cache_seconds = int(settings.get("health_cache_seconds", 5) or 5)
        now = time.monotonic()
        if self._cache and now - self._cache[0] < cache_seconds:
            return self._cache[1]

        snapshot = {
            "generated_at": self._now().isoformat(),
            "status": self._safe_read_section("status", self.read_status, {}),
            "life": self._safe_read_section("life", self.read_life, {}),
            "continuity": self._safe_read_section("continuity", self.read_continuity, {}),
            "qzone": self._safe_read_section("qzone", self.read_qzone, {}),
            "memory": self._safe_read_section("memory", self.read_memory, {}),
        }
        if isinstance(snapshot.get("status"), dict):
            snapshot["status"]["continuity"] = snapshot["continuity"]
        snapshot["health"] = self._safe_read_section("health", lambda: self.read_health(snapshot), {"ok": False})
        snapshot = self._redact_recursive(snapshot)
        self._cache = (now, snapshot)
        return snapshot

    def _safe_read_section(self, name: str, reader, fallback: dict[str, Any]) -> dict[str, Any]:
        try:
            value = reader()
            if isinstance(value, dict):
                return value
            return {"degraded": True, "error": f"{name}_invalid"}
        except Exception:
            degraded = dict(fallback)
            degraded.update({"degraded": True, "error": f"{name}_read_failed"})
            return degraded

    def read_status(self) -> dict[str, Any]:
        settings = self.dashboard_config_provider()
        life_file = self._first_existing(self._shared_life_candidates("shared_life_context.json"))
        bridge_state = self._first_existing(self._bridge_state_candidates())
        bridge_config = self._bridge_config()
        qzone_health = self._qzone_health(bridge_config)
        return {
            "now": self._now().isoformat(),
            "webui": "running",
            "refresh_interval_seconds": int(settings.get("refresh_interval_seconds", 10) or 10),
            "shared_life_context_found": life_file is not None,
            "qzone_bridge_found": bool(bridge_state or bridge_config),
            "qzone_auto_like_found": qzone_health["found"],
            "dry_run": bool(bridge_config.get("dry_run", True)),
            "bridge_enabled": bool(bridge_config.get("enabled", False)),
            "bind_host": settings.get("bind_host", "127.0.0.1"),
        }

    def read_life(self) -> dict[str, Any]:
        context_data = self._read_json_first(self._shared_life_candidates("shared_life_context.json"), {})
        shared_config = self._shared_config()
        daily_plan = context_data.get("daily_plan") if isinstance(context_data, dict) else {}
        if not isinstance(daily_plan, dict):
            daily_plan = {}
        period = {
            "current_period": self._clean_scalar(context_data.get("current_period")),
            "last_period_key": self._clean_scalar(context_data.get("last_period_key")),
            "last_period_refresh_at": self._clean_scalar(context_data.get("last_period_refresh_at")),
            "current_activity": self._current_activity(context_data.get("current_activity")),
            "micro_experience": self._clean_scalar(context_data.get("micro_experience")),
            "ambient_mood": self._clean_scalar(context_data.get("ambient_mood")),
            "life_state": self._clean_scalar(context_data.get("life_state")),
            "energy_level": self._clean_scalar(context_data.get("energy_level")),
            "activity_hint": self._clean_scalar(context_data.get("activity_hint")),
            "mood_hint": self._clean_scalar(context_data.get("mood_hint")),
            "social_hint": self._clean_scalar(context_data.get("social_hint")),
            "relationship_hint": self._clean_scalar(context_data.get("relationship_hint")),
        }
        period_times = self._parse_period_times(shared_config.get("period_refresh_times", DEFAULT_PERIOD_REFRESH_TIMES))
        expected_key = self._expected_period_key(period_times)
        stale = self._period_stale(period, expected_key, period_times)
        return {
            "daily": {
                "daily_plan": {
                    "morning": self._clean_scalar(daily_plan.get("morning")),
                    "afternoon": self._clean_scalar(daily_plan.get("afternoon")),
                    "evening": self._clean_scalar(daily_plan.get("evening")),
                    "night": self._clean_scalar(daily_plan.get("night")),
                },
                "last_daily_plan_refresh_at": self._clean_scalar(context_data.get("last_daily_plan_refresh_at")),
                "last_auto_refresh_at": self._clean_scalar(context_data.get("last_auto_refresh_at")),
            },
            "period": period,
            "refresh": {
                "auto_refresh_time": self._clean_scalar(shared_config.get("auto_refresh_time", "04:00")),
                "period_refresh_times": period_times,
                "next_daily_refresh_at": self._next_daily_refresh(shared_config),
                "next_period_refresh_at": self._next_period_refresh(period_times),
            },
            "stale_warning": stale,
        }

    def read_qzone(self) -> dict[str, Any]:
        settings = self.dashboard_config_provider()
        bridge_config = self._bridge_config()
        bridge_state = self._read_json_first(self._bridge_state_candidates(), {})
        if not isinstance(bridge_state, dict):
            bridge_state = {}
        windows = self._parse_windows(bridge_config.get("post_windows", DEFAULT_WINDOWS))
        history_limit = int(settings.get("history_limit", 20) or 20)
        prediction = self._prediction(bridge_config, bridge_state, windows)
        health = self._qzone_health(bridge_config)
        today_check_timeline = self._today_check_timeline(bridge_state.get("history"), history_limit)
        return {
            "bridge": {
                "enabled": bool(bridge_config.get("enabled", False)),
                "dry_run": bool(bridge_config.get("dry_run", True)),
                "check_interval_minutes": int(bridge_config.get("check_interval_minutes", 17) or 17),
                "post_windows": windows,
                "max_posts_per_day": int(bridge_config.get("max_posts_per_day", 1) or 1),
                "min_hours_between_posts": int(bridge_config.get("min_hours_between_posts", 8) or 8),
                "today_post_count": int(bridge_state.get("today_post_count", 0) or 0),
                "last_check_at": self._clean_scalar(bridge_state.get("last_check_at")),
                "last_post_at": self._clean_scalar(bridge_state.get("last_post_at")),
                "last_window": self._clean_scalar(bridge_state.get("last_window")),
                "last_generated_text": self._clean_scalar(bridge_state.get("last_generated_text")),
                "last_error": self._clean_scalar(bridge_state.get("last_error")),
            },
            "prediction": prediction,
            "health": health,
            "history": self._history(bridge_state.get("history"), history_limit),
            "today_check_timeline": today_check_timeline,
        }

    def read_memory(self) -> dict[str, Any]:
        settings = self.dashboard_config_provider()
        if not bool(settings.get("show_memory", True)):
            return {"enabled": False, "recent_traces": [], "days": [], "repeat_rate_24h": 0, "overfit_warning": False}
        memory_data = self._read_json_first(self._shared_life_candidates("shared_life_memory.json"), {})
        recent_traces = self._extract_recent_traces(memory_data)[:5]
        days = self._extract_days(memory_data)
        rate = self._repeat_rate_24h(memory_data)
        return {
            "enabled": True,
            "recent_traces": recent_traces,
            "days": days,
            "repeat_rate_24h": rate,
            "overfit_warning": rate >= 0.4,
            "note": "可能过拟合" if rate >= 0.4 else "",
        }

    def read_health(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        life = snapshot.get("life", {})
        qzone = snapshot.get("qzone", {})
        memory = snapshot.get("memory", {})
        bridge = qzone.get("bridge", {})
        prediction = qzone.get("prediction", {})
        warnings: list[dict[str, str]] = []
        if life.get("stale_warning", {}).get("stale"):
            warnings.append({"level": "yellow", "kind": "period_stale", "message": life["stale_warning"]["message"]})
        max_posts = int(bridge.get("max_posts_per_day", 1) or 1)
        today_count = int(bridge.get("today_post_count", 0) or 0)
        if today_count > max_posts:
            warnings.append({"level": "red", "kind": "quota_exceeded", "message": "today_post_count 超过 max_posts_per_day"})
        consecutive_errors = self._consecutive_errors(qzone.get("history", []), bridge.get("last_error"))
        if consecutive_errors > 3:
            warnings.append({"level": "red", "kind": "bridge_errors", "message": f"last_error 连续失败超过 3 次：{consecutive_errors}"})
        if memory.get("overfit_warning"):
            warnings.append({"level": "yellow", "kind": "memory_repeat", "message": "最近 24h 微体验重复率偏高，可能过拟合"})
        return {
            "ok": not any(item["level"] == "red" for item in warnings),
            "warnings": warnings,
            "consecutive_errors": consecutive_errors,
            "memory_repeat_rate_24h": memory.get("repeat_rate_24h", 0),
            "bridge_chance_score": prediction.get("chance_score", "Low"),
        }

    def read_continuity(self) -> dict[str, Any]:
        slc = self._continuity_slc()
        inner = self._read_inner_continuity()
        memory = self._read_aling_memory()
        health = self._continuity_health(slc, inner, memory)
        return {
            "status": health.get("status", "unknown"),
            "relationship": self._continuity_relationship(),
            "slc": slc,
            "inner_continuity": inner,
            "aling_memory": memory,
            "health": health,
        }

    def read_continuity_content(self) -> dict[str, Any]:
        relationship = {
            "title": "SLC / Inner Continuity / Aling Memory 的关系",
            "text": "SLC 是今日生活背景，Inner Continuity 是短期余波，Aling Memory 是长期小记忆和 recent_trace。",
            "relationship_text": (
                "SLC：今天阿绫处在什么生活背景里。Inner Continuity：刚刚几轮聊完后，她心里还残留什么。"
                "Aling Memory：更长期的小记忆、用户偏好、最近 24-72h 的话题残影。"
            ),
            "boundary_text": (
                "回复时，用户最新消息永远优先；Inner Continuity 和 Aling Memory 只能轻轻补一点连续感；"
                "SLC 只提供背景气氛，不能抢走聊天主线。"
            ),
            "flow": [
                "用户最新消息",
                "最近上下文",
                "Inner Continuity：刚刚几轮余波",
                "Aling Memory：长期小记忆 / recent_trace",
                "SLC：今日生活背景",
                "最终回复",
            ],
        }
        inner = self._inner_continuity_content()
        memory = self._aling_memory_content()
        slc_relation = self._slc_content_relation()
        payload = {
            "ok": True,
            "generated_at": self._now().isoformat(),
            "relationship": relationship,
            "relationship_text": relationship["relationship_text"],
            "flow": relationship["flow"],
            "inner_continuity": inner,
            "aling_memory": memory,
            "slc_relation": slc_relation,
        }
        return self._redact_recursive(payload)

    def read_continuity_debug(self) -> dict[str, Any]:
        inner_scan = self._scan_plugin_jsons(
            INNER_CONTINUITY_PLUGIN_NAME,
            ("", "data", "data/inner_continuity", "inner_continuity", "states", "state"),
            max_parse=10,
        )
        memory_scan = self._scan_plugin_jsons(ALING_MEMORY_PLUGIN_NAME, ("",), max_parse=20)
        expected = {
            filename: "found" if self._pick_scanned_file(memory_scan, filename, self._memory_file_keywords(filename)) else "missing"
            for filename in (
                "memory_store.json",
                "user_life_mirror.json",
                "context_summaries.json",
                "recent_trace.json",
                "flashback_state.json",
            )
        }
        payload = {
            "ok": True,
            "generated_at": self._now().isoformat(),
            "inner_continuity": self._scan_public(inner_scan),
            "aling_memory": {
                **self._scan_public(memory_scan),
                "expected_files": expected,
            },
        }
        return self._redact_recursive(payload)

    def _slc_content_relation(self) -> dict[str, Any]:
        slc = self._continuity_slc()
        inner_config = self._inner_config()
        inner_reads = inner_config.get("read_shared_life_context")
        return {
            "status": slc.get("status", "unknown"),
            "inner_reads_slc": True if inner_reads is True else False if inner_reads is False else "unknown",
            "message": (
                "Inner Continuity 当前会只读 SLC 摘要。"
                if inner_reads is True
                else "Inner Continuity 当前不会读取 SLC。"
                if inner_reads is False
                else "Inner Continuity 是否读取 SLC 未知。"
            ),
            "slc_fields_used_as_background": [
                "current_activity",
                "energy_level",
                "ambient_mood",
                "current_period",
            ],
            "background": {
                "current_activity": slc.get("current_activity"),
                "energy_level": slc.get("energy_level"),
                "ambient_mood": slc.get("ambient_mood"),
                "current_period": slc.get("current_period"),
            },
            "note": "SLC 只负责阿绫今天的生活状态，不负责记用户的事。它应该作为背景气氛，而不是每次回复都强行提到。",
        }

    def _inner_continuity_content(self) -> dict[str, Any]:
        summary = self._read_inner_continuity()
        scan = self._scan_plugin_jsons(
            INNER_CONTINUITY_PLUGIN_NAME,
            ("", "data", "data/inner_continuity", "inner_continuity", "states", "state"),
            max_parse=10,
        )
        errors = list(summary.get("errors") or [])
        warnings = list(summary.get("warnings") or [])
        errors.extend(scan.get("errors", []))
        diagnostics = list(scan.get("diagnostics", []))
        states: list[dict[str, Any]] = []
        now = self._now()
        for parsed in scan.get("_parsed", [])[:10]:
            path = parsed["path"]
            data = parsed["data"]
            if not isinstance(data, dict):
                continue
            source = self._inner_state_source(data)
            updated = (
                self._parse_datetime(self._deep_first(source, ("updated_at", "last_updated_at", "time", "timestamp")))
                or self._latest_datetime(source)
                or parsed["mtime"]
                or datetime.min.replace(tzinfo=self._timezone())
            )
            ttl_minutes = self._state_ttl_minutes(data, summary.get("config", {}))
            expired = updated != datetime.min.replace(tzinfo=self._timezone()) and now - updated > timedelta(minutes=ttl_minutes)
            scope = self._deep_first(source, ("scope", "scope_id", "session_id", "user_id", "conversation_id", "origin", "unified_msg_origin")) or self._scope_from_data_or_path(data, path)
            states.append(
                {
                    "scope": self._redact_identifier(scope),
                    "source_file": path.name,
                    "updated_at": updated.isoformat() if updated.year > 1900 else "",
                    "expired": expired,
                    "ttl_minutes": ttl_minutes,
                    "mood_hint": self._content_preview(self._deep_first(source, ("mood_hint", "mood", "mood_state", "emotion"))),
                    "residue": self._content_preview(self._deep_first(source, ("residue", "residues", "emotional_residue", "pending_reactions")), max_items=5),
                    "micro_details": self._content_preview(self._deep_first(source, ("micro_details", "micro_detail", "details", "recent_details")), max_items=5),
                    "flashback_candidates": self._content_preview(self._deep_first(source, ("flashback_candidates", "flashbacks", "flashback", "candidates")), max_items=5),
                    "cooldown": self._content_preview(self._deep_first(source, ("cooldown", "cooldowns", "flashback_cooldown"))),
                    "last_flashback_at": self._content_preview(self._deep_first(source, ("last_flashback_at", "last_flashback"))),
                    "raw_preview": self._structured_preview(data, max_items=50),
                }
            )
        status = self._content_status(summary, scan, errors)
        return {
            "status": status,
            "config": summary.get("config", {}),
            **self._scan_public(scan),
            "states": states,
            "raw_files": scan.get("raw_files", []),
            "warnings": warnings,
            "errors": errors,
            "diagnostics": diagnostics,
            "empty_message": "暂无数据，未找到 Inner Continuity 状态 JSON，或状态文件里没有可读对象。" if not states else "",
        }

    def _aling_memory_content(self) -> dict[str, Any]:
        summary = self._read_aling_memory()
        scan = self._scan_plugin_jsons(ALING_MEMORY_PLUGIN_NAME, ("",), max_parse=20)
        errors = list(summary.get("errors") or [])
        warnings = list(summary.get("warnings") or [])
        errors.extend(scan.get("errors", []))
        diagnostics = list(scan.get("diagnostics", []))
        parsed_by_path = {str(item["path"]).lower(): item["data"] for item in scan.get("_parsed", [])}
        file_map = {
            "memory_store": self._pick_scanned_file(scan, "memory_store.json", ("memory", "store", "memories")),
            "user_life_mirror": self._pick_scanned_file(scan, "user_life_mirror.json", ("mirror", "user_life", "life_mirror")),
            "context_summaries": self._pick_scanned_file(scan, "context_summaries.json", ("summary", "summaries", "context")),
            "recent_trace": self._pick_scanned_file(scan, "recent_trace.json", ("recent", "trace")),
            "flashback_state": self._pick_scanned_file(scan, "flashback_state.json", ("flashback", "state")),
            "config": self._pick_scanned_file(scan, "config.json", ("config",)),
        }
        loaded: dict[str, Any] = {}
        for name, path in file_map.items():
            if path is None:
                diagnostics.append(f"{name}: file not found")
                loaded[name] = {}
                continue
            data = parsed_by_path.get(str(path).lower())
            if data is None:
                data, error = self._read_json_file_safe(path, {})
                if error:
                    errors.append(f"{self._public_path(path)}: {error}")
                    data = {}
                elif self._public_path(path) not in scan["readable_files"]:
                    scan["readable_files"].append(self._public_path(path))
            loaded[name] = data
            diagnostics.append(f"{name}: {self._public_path(path)}")
        status = self._content_status(summary, scan, errors)
        memory_store = self._memory_store_entries(loaded.get("memory_store"), limit=20)
        context_summaries = self._summary_entries(loaded.get("context_summaries"), limit=20)
        recent_trace = self._recent_trace_entries(loaded.get("recent_trace"), limit=20)
        expected_files = {
            filename: "found" if self._pick_scanned_file(scan, filename, self._memory_file_keywords(filename)) else "missing"
            for filename in (
                "memory_store.json",
                "user_life_mirror.json",
                "context_summaries.json",
                "recent_trace.json",
                "flashback_state.json",
            )
        }
        return {
            "status": status,
            "config": summary.get("config", {}),
            **self._scan_public(scan),
            "expected_files": expected_files,
            "memory_store": memory_store,
            "memory_store_raw": self._structured_preview(loaded.get("memory_store"), max_items=80),
            "user_life_mirror_raw": self._structured_preview(loaded.get("user_life_mirror"), max_items=80),
            "user_life_mirror": self._structured_preview(loaded.get("user_life_mirror"), max_items=60),
            "context_summaries": context_summaries,
            "context_summaries_raw": self._structured_preview(loaded.get("context_summaries"), max_items=80),
            "recent_trace": recent_trace,
            "recent_trace_raw": self._structured_preview(loaded.get("recent_trace"), max_items=80),
            "flashback_state": self._flashback_state_preview(loaded.get("flashback_state"), summary.get("config", {})),
            "flashback_state_raw": self._structured_preview(loaded.get("flashback_state"), max_items=80),
            "raw_files": scan.get("raw_files", []),
            "warnings": warnings,
            "errors": errors,
            "diagnostics": diagnostics,
            "empty_message": "暂无数据，插件可能尚未生成数据文件。",
        }

    def _continuity_relationship(self) -> dict[str, Any]:
        matrix = [
            {
                "module": "shared_life_context",
                "responsibility": "阿绫今日生活状态",
                "reads": "无或自身数据",
                "writes": "SLC 自己",
                "injection": "shared_life_context",
                "risk": "被过度当成聊天主线",
            },
            {
                "module": "inner_continuity",
                "responsibility": "刚刚几轮心理余波",
                "reads": "可只读 SLC",
                "writes": "自己状态文件",
                "injection": "inner_continuity",
                "risk": "闪回过频、短期状态过重",
            },
            {
                "module": "aling_memory",
                "responsibility": "长期小记忆、recent_trace、Mirror",
                "reads": "自己数据",
                "writes": "自己数据",
                "injection": "aling_relevant_memory / recent_continuity / user_life_mirror_slice",
                "risk": "token 增加、记忆污染",
            },
        ]
        return {
            "summary": (
                "SLC provides today's living background. Inner Continuity may read a short SLC summary "
                "to tune short-term tone. Aling Memory owns long-term small memories, 24-72h recent_trace, "
                "and User Life Mirror. All three should influence replies briefly, sparingly, and relevantly."
            ),
            "layers": [
                {
                    "name": "shared_life_context",
                    "role": "今日生活舞台",
                    "reads": [],
                    "writes": ["shared_life_context"],
                    "should_not_write": ["aling_memory", "inner_continuity"],
                },
                {
                    "name": "inner_continuity",
                    "role": "刚刚几轮的心理余波",
                    "reads": ["shared_life_context summary when enabled"],
                    "writes": ["inner_continuity state"],
                    "should_not_write": ["shared_life_context", "aling_memory"],
                },
                {
                    "name": "aling_memory",
                    "role": "长期小记忆 / recent_trace / User Life Mirror",
                    "reads": ["aling_memory store"],
                    "writes": ["aling_memory store"],
                    "should_not_write": ["shared_life_context"],
                },
            ],
            "matrix": matrix,
            "boundary_note": (
                "三者都只应该短、少、相关地影响回复；如果阿绫频繁提旧事、过度围绕当前活动、"
                "或 token 增加，应优先检查这里的注入预算和闪回冷却。"
            ),
        }

    def _continuity_slc(self) -> dict[str, Any]:
        path, data, error = self._read_json_first_with_meta(self._shared_life_candidates("shared_life_context.json"), {})
        config = self._shared_config()
        inner_config = self._inner_config()
        read_slc = inner_config.get("read_shared_life_context") if inner_config else None
        current_activity = self._current_activity(data.get("current_activity") if isinstance(data, dict) else None)
        last_refresh = ""
        if isinstance(data, dict):
            last_refresh = self._clean_scalar(
                data.get("last_refresh_at")
                or data.get("last_period_refresh_at")
                or data.get("last_daily_plan_refresh_at")
                or data.get("updated_at")
            )
        status = "ok" if path and not error else "missing" if not path else "degraded"
        message = "Inner Continuity read mode unknown"
        if read_slc is True:
            message = "Inner Continuity is reading a short SLC summary."
        elif read_slc is False:
            message = "Inner Continuity is not reading SLC."
        return {
            "detected": bool(path),
            "status": status,
            "last_refresh_at": last_refresh,
            "current_activity": current_activity,
            "micro_experience": self._clean_scalar(data.get("micro_experience") if isinstance(data, dict) else ""),
            "energy_level": self._clean_scalar(data.get("energy_level") if isinstance(data, dict) else ""),
            "ambient_mood": self._clean_scalar(data.get("ambient_mood") if isinstance(data, dict) else ""),
            "current_period": self._clean_scalar(data.get("current_period") if isinstance(data, dict) else ""),
            "data_source": str(path) if path else "",
            "last_error": error,
            "read_by": {
                "inner_continuity": True if read_slc is True else False if read_slc is False else "unknown",
                "inner_continuity_message": message,
                "aling_memory": False,
                "aling_memory_message": "aling_memory should not directly write or overwrite SLC.",
            },
            "config_hint": {
                "auto_refresh_time": self._clean_scalar(config.get("auto_refresh_time")),
                "period_refresh_times": config.get("period_refresh_times", {}),
            },
        }

    def _read_inner_continuity(self) -> dict[str, Any]:
        config_path, config, config_error = self._read_json_first_with_meta(
            self._config_candidates(INNER_CONTINUITY_PLUGIN_NAME),
            {},
        )
        plugin_dirs = self._candidate_plugin_dirs(INNER_CONTINUITY_PLUGIN_NAME)
        plugin_exists = any(directory.exists() for directory in plugin_dirs)
        data_dirs = self._inner_state_dirs()
        data_dir = next((directory for directory in data_dirs if directory.exists()), data_dirs[0] if data_dirs else None)
        errors = [config_error] if config_error else []
        warnings: list[str] = []
        if config_path is None:
            warnings.append("inner_continuity config not found")
        config_public = self._pick_config(
            config,
            (
                "enabled",
                "inject_enabled",
                "update_enabled",
                "use_llm_update",
                "read_shared_life_context",
                "max_residue_items",
                "max_micro_details",
                "max_flashback_candidates",
                "default_ttl_minutes",
                "strong_ttl_minutes",
                "max_injected_chars",
                "min_update_interval_seconds",
                "flashback_cooldown_seconds",
                "debug",
            ),
        )
        metrics = {
            "state_file_count": 0,
            "latest_state_updated_at": "",
            "active_state_count": 0,
            "expired_or_old_state_count": 0,
            "total_residue_items": 0,
            "total_micro_details": 0,
            "total_flashback_candidates": 0,
        }
        latest_summary: dict[str, Any] = {}
        if data_dir is not None:
            try:
                state_files = list(data_dir.rglob("*.json")) if data_dir.exists() else []
                metrics["state_file_count"] = len(state_files)
                latest_data: dict[str, Any] | None = None
                latest_at: datetime | None = None
                now = self._now()
                for state_file in state_files:
                    state_data, state_error = self._read_json_file_safe(state_file, {})
                    if state_error:
                        errors.append(f"{state_file.name}: {state_error}")
                        continue
                    if not isinstance(state_data, dict):
                        continue
                    updated = self._latest_datetime(state_data) or self._file_mtime_datetime(state_file)
                    ttl_minutes = self._state_ttl_minutes(state_data, config_public)
                    expired = bool(updated and now - updated > timedelta(minutes=ttl_minutes))
                    if expired:
                        metrics["expired_or_old_state_count"] += 1
                    else:
                        metrics["active_state_count"] += 1
                    metrics["total_residue_items"] += self._len_field(state_data, "residue")
                    metrics["total_micro_details"] += self._len_field(state_data, "micro_details")
                    metrics["total_flashback_candidates"] += self._len_field(state_data, "flashback_candidates")
                    if updated and (latest_at is None or updated > latest_at):
                        latest_at = updated
                        latest_data = state_data
                if latest_at is not None:
                    metrics["latest_state_updated_at"] = latest_at.isoformat()
                if latest_data is not None:
                    latest_summary = {
                        "mood_hint": self._clean_scalar(latest_data.get("mood_hint"), limit=120),
                        "residue_count": self._len_field(latest_data, "residue"),
                        "micro_details_count": self._len_field(latest_data, "micro_details"),
                        "flashback_candidates_count": self._len_field(latest_data, "flashback_candidates"),
                        "cooldown_summary": self._cooldown_summary(latest_data),
                        "ttl_summary": self._ttl_summary(latest_data, config_public),
                        "last_updated_at": metrics["latest_state_updated_at"],
                    }
            except PermissionError as exc:
                errors.append(f"data directory permission denied: {exc}")
            except Exception as exc:
                errors.append(f"data directory read failed: {exc}")
        enabled = bool(config_public.get("enabled", False))
        status = self._module_status(plugin_exists, enabled, errors, config_path, data_dir)
        return {
            "status": status,
            "detected": plugin_exists or config_path is not None,
            "config_source": str(config_path) if config_path else "",
            "data_source": str(data_dir) if data_dir else "",
            "config": config_public,
            "metrics": metrics,
            "latest_summary": latest_summary,
            "warnings": warnings,
            "errors": errors,
            "description": (
                "Inner Continuity stores short-term psychological residue only. It may read a short SLC summary, "
                "but it should not write SLC or aling_memory."
            ),
        }

    def _read_aling_memory(self) -> dict[str, Any]:
        config_path, config, config_error = self._read_json_first_with_meta(
            self._config_candidates(ALING_MEMORY_PLUGIN_NAME),
            {},
        )
        plugin_dirs = self._candidate_plugin_dirs(ALING_MEMORY_PLUGIN_NAME)
        plugin_dir = next((directory for directory in plugin_dirs if directory.exists()), plugin_dirs[0] if plugin_dirs else None)
        plugin_exists = plugin_dir is not None and plugin_dir.exists()
        errors = [config_error] if config_error else []
        warnings: list[str] = []
        if config_path is None:
            warnings.append("aling_memory config not found")
        config_public = self._pick_config(
            config,
            (
                "enabled",
                "auto_extract_enabled",
                "auto_confirm_safe_preferences",
                "context_summary_enabled",
                "mirror_enabled",
                "debug_enabled",
                "summary_every_n_turns",
                "summary_ttl_days",
                "mirror_refresh_min_hours",
                "allow_auto_overwrite_manual_mirror",
                "recent_trace_enabled",
                "recent_trace_ttl_hours",
                "recent_trace_max_items_per_session",
                "recent_trace_inject_max_items",
                "recent_trace_inject_max_chars",
                "recent_trace_min_importance",
                "max_memory_items_total",
                "max_candidates_total",
                "flashback_min_turn_gap",
                "same_memory_min_hours",
                "max_flashback_per_day",
            ),
        )
        files: dict[str, dict[str, Any]] = {}
        data: dict[str, Any] = {}
        for filename in (
            "memory_store.json",
            "user_life_mirror.json",
            "context_summaries.json",
            "flashback_state.json",
            "recent_trace.json",
            "config.json",
        ):
            path = self._find_plugin_file(ALING_MEMORY_PLUGIN_NAME, filename)
            if path is None:
                files[filename] = {"exists": False, "readable": False, "path": "", "error": "missing"}
                continue
            value, error = self._read_json_file_safe(path, {})
            files[filename] = {"exists": True, "readable": error == "", "path": str(path), "error": error}
            if error:
                errors.append(f"{filename}: {error}")
            else:
                data[filename] = value
        metrics = self._aling_memory_metrics(data)
        enabled = bool(config_public.get("enabled", False))
        status = self._module_status(plugin_exists, enabled, errors, config_path, plugin_dir)
        if not any(info["exists"] for info in files.values()) and plugin_exists:
            warnings.append("aling_memory data files have not been generated yet")
        elif any(info["exists"] and not info["readable"] for info in files.values()):
            warnings.append("some aling_memory data files are degraded")
        return {
            "status": status,
            "detected": plugin_exists or config_path is not None,
            "config_source": str(config_path) if config_path else "",
            "data_source": str(plugin_dir) if plugin_dir else "",
            "config": config_public,
            "files": files,
            "metrics": metrics,
            "warnings": warnings,
            "errors": errors,
            "description": (
                "Aling Memory owns long-term small memories, User Life Mirror, and 24-72h recent_trace. "
                "It should not become an open-loop task tracker and should not write shared_life_context."
            ),
            "injection_budget": {
                "recent_trace_inject_max_items": config_public.get("recent_trace_inject_max_items"),
                "recent_trace_inject_max_chars": config_public.get("recent_trace_inject_max_chars"),
                "max_flashback_per_day": config_public.get("max_flashback_per_day"),
                "same_memory_min_hours": config_public.get("same_memory_min_hours"),
            },
        }

    def _continuity_health(self, slc: dict[str, Any], inner: dict[str, Any], memory: dict[str, Any]) -> dict[str, Any]:
        checks: list[dict[str, str]] = []
        inner_config = inner.get("config", {})
        memory_config = memory.get("config", {})
        read_slc = inner_config.get("read_shared_life_context")
        checks.append(
            {
                "name": "inner_reads_slc",
                "status": "ok" if read_slc is True else "info",
                "message": (
                    "Inner Continuity is reading a short SLC summary."
                    if read_slc is True
                    else "Inner Continuity is not reading SLC, so short-term tone will not reference today's living state."
                ),
            }
        )
        max_injected = self._int_or_none(inner_config.get("max_injected_chars"))
        if max_injected is None or max_injected <= 900:
            budget_status = "ok"
        elif max_injected <= 1400:
            budget_status = "warn"
        else:
            budget_status = "risk"
        checks.append(
            {
                "name": "inner_injection_budget",
                "status": budget_status,
                "message": f"Inner Continuity max_injected_chars={max_injected if max_injected is not None else 'unknown'}.",
            }
        )
        rt_chars = self._int_or_none(memory_config.get("recent_trace_inject_max_chars"))
        rt_items = self._int_or_none(memory_config.get("recent_trace_inject_max_items"))
        if (rt_chars is None or rt_chars <= 800) and (rt_items is None or rt_items <= 3):
            rt_status = "ok"
        elif (rt_chars is not None and rt_chars > 1200) or (rt_items is not None and rt_items > 5):
            rt_status = "risk"
        else:
            rt_status = "warn"
        checks.append(
            {
                "name": "memory_recent_trace_budget",
                "status": rt_status,
                "message": f"recent_trace budget chars={rt_chars if rt_chars is not None else 'unknown'}, items={rt_items if rt_items is not None else 'unknown'}.",
            }
        )
        flash_gap = self._int_or_none(memory_config.get("flashback_min_turn_gap"))
        same_hours = self._int_or_none(memory_config.get("same_memory_min_hours"))
        max_per_day = self._int_or_none(memory_config.get("max_flashback_per_day"))
        flash_ok = (
            (flash_gap is None or flash_gap >= 8)
            and (same_hours is None or same_hours >= 24)
            and (max_per_day is None or max_per_day <= 5)
        )
        checks.append(
            {
                "name": "memory_flashback_rate",
                "status": "ok" if flash_ok else "warn",
                "message": f"flashback_min_turn_gap={flash_gap}, same_memory_min_hours={same_hours}, max_flashback_per_day={max_per_day}.",
            }
        )
        activity = self._current_activity(slc.get("current_activity", {})).get("value", "")
        micro = str(slc.get("micro_experience") or "")
        ambient = str(slc.get("ambient_mood") or "")
        slc_warn = len(activity) > 160 or len(micro) > 180 or (not activity and not micro and len(ambient) > 100)
        checks.append(
            {
                "name": "slc_overuse_risk",
                "status": "warn" if slc_warn else "info",
                "message": (
                    "SLC may be too strong or sparse; keep it as a background layer, not the chat main line."
                    if slc_warn
                    else "SLC looks suitable as a lightweight background layer."
                ),
            }
        )
        inner_flashback_enabled = bool(inner_config.get("max_flashback_candidates", 0) or inner_config.get("flashback_cooldown_seconds"))
        memory_flashback_enabled = bool(memory_config.get("recent_trace_enabled") or memory_config.get("max_flashback_per_day"))
        checks.append(
            {
                "name": "overlap_warning",
                "status": "warn" if inner_flashback_enabled and memory_flashback_enabled else "info",
                "message": (
                    "Both Inner Continuity and Aling Memory may create a callback feeling. If old topics recur too often, lower Inner flashback_cooldown or Memory max_flashback_per_day."
                    if inner_flashback_enabled and memory_flashback_enabled
                    else "No obvious overlap pressure between short-term residue and longer recent_trace."
                ),
            }
        )
        overall = "ok"
        if any(check["status"] == "risk" for check in checks):
            overall = "risk"
        elif any(check["status"] == "warn" for check in checks):
            overall = "warn"
        return {
            "status": overall,
            "summary": (
                "SLC, Inner Continuity, and Aling Memory boundaries look clear."
                if overall == "ok"
                else "Continuity stack has items worth reviewing."
            ),
            "checks": checks,
        }

    def _inner_config(self) -> dict[str, Any]:
        _path, config, _error = self._read_json_first_with_meta(self._config_candidates(INNER_CONTINUITY_PLUGIN_NAME), {})
        return config if isinstance(config, dict) else {}

    def _inner_state_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        for directory in self._candidate_plugin_dirs(INNER_CONTINUITY_PLUGIN_NAME):
            dirs.append(directory / "data" / "inner_continuity")
            dirs.append(directory / "inner_continuity")
            dirs.append(directory / "data")
        return self._dedupe_paths(dirs)

    def _pick_config(self, config: Any, keys: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(config, dict):
            return {}
        return {key: config.get(key) for key in keys if key in config}

    def _read_json_file_safe(self, path: Path, default: Any) -> tuple[Any, str]:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig")), ""
        except FileNotFoundError:
            return default, "missing"
        except PermissionError as exc:
            return default, f"permission_denied: {exc}"
        except json.JSONDecodeError as exc:
            return default, f"json_decode_error: {exc}"
        except Exception as exc:
            return default, f"read_error: {exc}"

    def _read_json_first_with_meta(self, candidates: list[Path], default: Any) -> tuple[Path | None, Any, str]:
        last_error = ""
        for path in candidates:
            try:
                if not path.exists() or not path.is_file():
                    continue
            except Exception as exc:
                last_error = f"{path}: stat_error: {exc}"
                continue
            data, error = self._read_json_file_safe(path, default)
            if not error:
                return path, data, ""
            last_error = f"{path}: {error}"
        return None, default, last_error

    def _module_status(
        self,
        plugin_exists: bool,
        enabled: bool,
        errors: list[str],
        config_path: Path | None,
        data_path: Path | None,
    ) -> str:
        if errors:
            return "degraded"
        if not plugin_exists and config_path is None:
            return "missing"
        if config_path is not None and not enabled:
            return "disabled"
        if plugin_exists or data_path is not None or config_path is not None:
            return "ok" if enabled else "unknown"
        return "missing"

    def _find_plugin_file(self, plugin_name: str, filename: str) -> Path | None:
        for directory in self._candidate_plugin_dirs(plugin_name):
            for candidate in (directory / filename, directory / "data" / filename):
                try:
                    if candidate.exists() and candidate.is_file():
                        return candidate
                except Exception:
                    continue
        return None

    def _len_field(self, data: Any, key: str) -> int:
        if not isinstance(data, dict):
            return 0
        value = data.get(key)
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return 1 if value else 0

    def _state_ttl_minutes(self, data: dict[str, Any], config: dict[str, Any]) -> int:
        for key in ("ttl_minutes", "ttl", "default_ttl_minutes"):
            value = data.get(key) if key in data else config.get(key)
            parsed = self._int_or_none(value)
            if parsed is not None and parsed > 0:
                return parsed
        parsed = self._int_or_none(config.get("strong_ttl_minutes"))
        return parsed if parsed is not None and parsed > 0 else 60

    def _cooldown_summary(self, data: dict[str, Any]) -> dict[str, Any]:
        cooldown = data.get("cooldown") or data.get("cooldowns") or data.get("flashback_cooldown")
        if isinstance(cooldown, dict):
            return {"configured": True, "keys": list(cooldown.keys())[:8], "count": len(cooldown)}
        if cooldown:
            return {"configured": True, "value": self._clean_scalar(cooldown, limit=80)}
        return {"configured": False}

    def _ttl_summary(self, data: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        updated = self._latest_datetime(data)
        ttl_minutes = self._state_ttl_minutes(data, config)
        expired = False
        if updated is not None:
            expired = self._now() - updated > timedelta(minutes=ttl_minutes)
        return {"ttl_minutes": ttl_minutes, "expired": expired}

    def _latest_datetime(self, data: Any) -> datetime | None:
        latest: datetime | None = None
        if isinstance(data, dict):
            for key, value in data.items():
                if key in ("updated_at", "last_updated_at", "created_at", "last_refresh_at", "at", "time", "timestamp"):
                    parsed = self._parse_datetime(value)
                    if parsed is not None and (latest is None or parsed > latest):
                        latest = parsed
                nested = self._latest_datetime(value)
                if nested is not None and (latest is None or nested > latest):
                    latest = nested
        elif isinstance(data, list):
            for item in data:
                nested = self._latest_datetime(item)
                if nested is not None and (latest is None or nested > latest):
                    latest = nested
        return latest

    def _file_mtime_datetime(self, path: Path) -> datetime | None:
        try:
            return datetime.fromtimestamp(path.stat().st_mtime, tz=self._timezone()).replace(microsecond=0)
        except Exception:
            return None

    def _aling_memory_metrics(self, data: dict[str, Any]) -> dict[str, Any]:
        memory_store = data.get("memory_store.json", {})
        mirror = data.get("user_life_mirror.json", {})
        summaries = data.get("context_summaries.json", {})
        flashback = data.get("flashback_state.json", {})
        recent_trace = data.get("recent_trace.json", {})
        return {
            "memory_scope_count": self._scope_count(memory_store),
            "memory_item_count": self._count_named_items(memory_store, ("items", "memories", "memory_items", "confirmed")),
            "candidate_count": self._count_named_items(memory_store, ("candidates", "pending", "candidate_items")),
            "mirror_slice_count": self._scope_count(mirror),
            "summary_count": self._generic_item_count(summaries),
            "recent_trace_scope_count": self._scope_count(recent_trace),
            "recent_trace_item_count": self._count_named_items(recent_trace, ("items", "traces", "recent_trace", "recent_traces")),
            "flashback_state_count": self._generic_item_count(flashback),
            "latest_memory_updated_at": self._format_dt(self._latest_datetime(memory_store)),
            "latest_recent_trace_updated_at": self._format_dt(self._latest_datetime(recent_trace)),
            "type_distribution": self._type_distribution(memory_store),
        }

    def _scope_count(self, value: Any) -> int:
        if isinstance(value, dict):
            return len(value)
        if isinstance(value, list):
            return len(value)
        return 0

    def _generic_item_count(self, value: Any) -> int:
        if isinstance(value, list):
            return len(value)
        if isinstance(value, dict):
            total = 0
            for item in value.values():
                if isinstance(item, list):
                    total += len(item)
                elif isinstance(item, dict):
                    total += self._generic_item_count(item)
                elif item:
                    total += 1
            return total if total else len(value)
        return 0

    def _count_named_items(self, value: Any, names: tuple[str, ...]) -> int:
        if isinstance(value, list):
            return len(value)
        if not isinstance(value, dict):
            return 0
        total = 0
        found_named = False
        for key, item in value.items():
            if key in names:
                found_named = True
                total += self._generic_item_count(item)
            elif isinstance(item, dict):
                total += self._count_named_items(item, names)
        if total == 0 and not found_named and value:
            return self._generic_item_count(value)
        return total

    def _type_distribution(self, value: Any) -> dict[str, int]:
        counts: dict[str, int] = {}
        if isinstance(value, dict):
            item_type = value.get("type") or value.get("kind") or value.get("category")
            if item_type:
                key = self._clean_scalar(item_type, limit=40)
                counts[key] = counts.get(key, 0) + 1
            for item in value.values():
                nested = self._type_distribution(item)
                for key, count in nested.items():
                    counts[key] = counts.get(key, 0) + count
        elif isinstance(value, list):
            for item in value:
                nested = self._type_distribution(item)
                for key, count in nested.items():
                    counts[key] = counts.get(key, 0) + count
        return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True)[:12])

    def _int_or_none(self, value: Any) -> int | None:
        try:
            return int(value)
        except Exception:
            return None

    def _format_dt(self, value: datetime | None) -> str:
        return value.isoformat() if value is not None else ""

    def _scan_plugin_jsons(self, plugin_name: str, subdirs: tuple[str, ...], max_parse: int = 10) -> dict[str, Any]:
        searched_dirs = self._plugin_scan_dirs(plugin_name, subdirs)
        existing_dirs: list[Path] = []
        json_files: list[Path] = []
        errors: list[str] = []
        diagnostics: list[str] = []
        seen_files: set[str] = set()
        for directory in searched_dirs:
            try:
                exists = directory.exists() and directory.is_dir()
            except Exception as exc:
                errors.append(f"{self._public_path(directory)}: stat_error: {exc}")
                continue
            if not exists:
                diagnostics.append(f"{self._public_path(directory)}: missing")
                continue
            existing_dirs.append(directory)
            try:
                found = list(directory.rglob("*.json"))
            except PermissionError as exc:
                errors.append(f"{self._public_path(directory)}: permission_denied: {exc}")
                continue
            except Exception as exc:
                errors.append(f"{self._public_path(directory)}: scan_failed: {exc}")
                continue
            diagnostics.append(f"{self._public_path(directory)}: exists, json_files={len(found)}")
            for path in found:
                key = self._path_key(path)
                if key in seen_files:
                    continue
                seen_files.add(key)
                json_files.append(path)
        json_files.sort(key=lambda path: self._file_mtime_datetime(path) or datetime.min.replace(tzinfo=self._timezone()), reverse=True)
        parsed: list[dict[str, Any]] = []
        raw_files: list[dict[str, Any]] = []
        readable_files: list[str] = []
        for path in json_files[:max_parse]:
            data, error = self._read_json_file_safe(path, {})
            if error:
                errors.append(f"{self._public_path(path)}: {error}")
                continue
            readable_files.append(self._public_path(path))
            mtime = self._file_mtime_datetime(path)
            parsed.append({"path": path, "data": data, "mtime": mtime})
            raw_files.append(
                {
                    "path": self._public_path(path),
                    "mtime": self._format_dt(mtime),
                    "preview": self._structured_preview(data, max_items=50),
                }
            )
        return {
            "searched_dirs": [self._public_path(path) for path in searched_dirs],
            "existing_dirs": [self._public_path(path) for path in existing_dirs],
            "json_files": [self._public_path(path) for path in json_files],
            "discovered_files": [self._public_path(path) for path in json_files],
            "readable_files": readable_files,
            "raw_files": raw_files,
            "errors": errors,
            "diagnostics": diagnostics,
            "_json_paths": json_files,
            "_parsed": parsed,
        }

    def _plugin_scan_dirs(self, plugin_name: str, subdirs: tuple[str, ...]) -> list[Path]:
        dirs: list[Path] = []
        for base in self._candidate_plugin_dirs(plugin_name):
            for rel in subdirs:
                dirs.append(base / rel if rel else base)
        return self._dedupe_paths(dirs)

    def _scan_public(self, scan: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in scan.items() if not key.startswith("_")}

    def _path_key(self, path: Path) -> str:
        try:
            return str(path.resolve()).lower()
        except Exception:
            return str(path).lower()

    def _public_path(self, path: Path) -> str:
        return self._redact_cookie_text(str(path))

    def _content_status(self, summary: dict[str, Any], scan: dict[str, Any], errors: list[str]) -> str:
        config = summary.get("config") if isinstance(summary, dict) else {}
        if isinstance(config, dict) and config.get("enabled") is False:
            return "disabled"
        if scan.get("readable_files"):
            return "degraded" if errors else "ok"
        if scan.get("discovered_files"):
            return "degraded"
        if scan.get("existing_dirs"):
            return "empty"
        return "missing"

    def _inner_state_source(self, data: dict[str, Any]) -> dict[str, Any]:
        for key in ("state", "content", "data"):
            value = data.get(key)
            if isinstance(value, dict):
                return value
        sessions = data.get("sessions")
        if isinstance(sessions, dict):
            for value in sessions.values():
                if isinstance(value, dict):
                    return value
        return data

    def _deep_first(self, value: Any, keys: tuple[str, ...]) -> Any:
        if isinstance(value, dict):
            for key in keys:
                if key in value and value[key] not in (None, ""):
                    return value[key]
            for item in value.values():
                found = self._deep_first(item, keys)
                if found not in (None, ""):
                    return found
        elif isinstance(value, list):
            for item in value:
                found = self._deep_first(item, keys)
                if found not in (None, ""):
                    return found
        return None

    def _pick_scanned_file(self, scan: dict[str, Any], exact_filename: str, keywords: tuple[str, ...]) -> Path | None:
        paths = scan.get("_json_paths", [])
        exact_lower = exact_filename.lower()
        for path in paths:
            if path.name.lower() == exact_lower:
                return path
        for path in paths:
            lowered = path.name.lower()
            if any(keyword in lowered for keyword in keywords):
                return path
        return None

    def _memory_file_keywords(self, filename: str) -> tuple[str, ...]:
        if filename == "memory_store.json":
            return ("memory", "store", "memories")
        if filename == "user_life_mirror.json":
            return ("mirror", "user_life", "life_mirror")
        if filename == "context_summaries.json":
            return ("summary", "summaries", "context")
        if filename == "recent_trace.json":
            return ("recent", "trace")
        if filename == "flashback_state.json":
            return ("flashback", "state")
        return (Path(filename).stem,)

    def _find_memory_file(self, filename: str, keywords: tuple[str, ...]) -> Path | None:
        exact = self._find_plugin_file(ALING_MEMORY_PLUGIN_NAME, filename)
        if exact is not None:
            return exact
        matches: list[Path] = []
        for directory in self._candidate_plugin_dirs(ALING_MEMORY_PLUGIN_NAME):
            try:
                files = list(directory.rglob("*.json")) if directory.exists() else []
            except Exception:
                continue
            for path in files:
                lowered = path.name.lower()
                if all(keyword in lowered for keyword in keywords):
                    matches.append(path)
        return sorted(matches, key=lambda path: len(str(path)))[0] if matches else None

    def _scope_from_data_or_path(self, data: dict[str, Any], path: Path) -> str:
        for key in ("scope", "scope_id", "session_id", "user_id", "conversation_id", "origin"):
            value = data.get(key)
            if value:
                return str(value)
        return path.stem

    def _redact_identifier(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "-"
        text = self._redact_cookie_text(text)
        if len(text) <= 4:
            return "***"
        if re.fullmatch(r"\d{5,}", text):
            return f"***{text[-3:]}"
        if re.search(r"\d{5,}", text):
            return re.sub(r"\d{5,}", lambda match: f"***{match.group(0)[-3:]}", text)
        if len(text) > 18:
            return f"{text[:6]}...{text[-4:]}"
        return text

    def _content_preview(self, value: Any, max_items: int = 5, max_chars: int = 500) -> dict[str, Any]:
        if value is None or value == "":
            return {"kind": "empty", "items": [], "text": "暂无"}
        if isinstance(value, list):
            items = [self._truncate_text(self._render_content_item(item), max_chars) for item in value[:max_items]]
            return {"kind": "list", "items": items, "truncated": len(value) > max_items}
        if isinstance(value, dict):
            entries = []
            for index, (key, item) in enumerate(value.items()):
                if index >= max_items:
                    break
                rendered = "<redacted>" if SENSITIVE_KEY_PATTERN.search(str(key)) else self._truncate_text(self._render_content_item(item), max_chars)
                entries.append({"key": self._redact_identifier(key), "value": rendered})
            return {"kind": "dict", "items": entries, "truncated": len(value) > max_items}
        return {"kind": "text", "text": self._truncate_text(self._render_content_item(value), max_chars)}

    def _render_content_item(self, value: Any) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(self._redact_recursive(value), ensure_ascii=False, separators=(",", ":"))
        return self._redact_cookie_text(str(value))

    def _truncate_text(self, value: str, max_chars: int = 500) -> str:
        text = self._redact_cookie_text(str(value or "")).replace("\r", " ").strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rstrip() + "... [truncated]"

    def _structured_preview(self, value: Any, max_items: int = 60) -> dict[str, Any]:
        return {"entries": self._flatten_preview(value, max_items=max_items), "empty": not bool(value)}

    def _flatten_preview(self, value: Any, prefix: str = "", max_items: int = 60) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        if len(rows) >= max_items:
            return rows
        if isinstance(value, dict):
            for key, item in value.items():
                if len(rows) >= max_items:
                    break
                label = f"{prefix}.{key}" if prefix else str(key)
                if isinstance(item, (dict, list)):
                    rows.extend(self._flatten_preview(item, label, max_items - len(rows)))
                else:
                    rendered = "<redacted>" if SENSITIVE_KEY_PATTERN.search(str(key)) else self._truncate_text(str(item), 500)
                    rows.append({"key": self._redact_identifier(label), "value": rendered})
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if len(rows) >= max_items:
                    break
                label = f"{prefix}[{index}]" if prefix else f"[{index}]"
                if isinstance(item, (dict, list)):
                    rows.extend(self._flatten_preview(item, label, max_items - len(rows)))
                else:
                    rows.append({"key": self._redact_identifier(label), "value": self._truncate_text(str(item), 500)})
        elif value:
            rows.append({"key": self._redact_identifier(prefix or "value"), "value": self._truncate_text(str(value), 500)})
        return rows[:max_items]

    def _iter_records(self, value: Any, scope: str = ""):
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield scope, item
        elif isinstance(value, dict):
            if self._looks_like_record(value):
                yield scope, value
            container_keys = {"scopes", "sessions", "data", "items", "memories", "memory_store", "records", "entries"}
            for key, item in value.items():
                next_scope = scope
                if key not in container_keys and not self._looks_like_record(item if isinstance(item, dict) else {}):
                    next_scope = str(key) if not scope else scope
                if isinstance(item, list):
                    for child in item:
                        if isinstance(child, dict):
                            yield next_scope, child
                elif isinstance(item, dict):
                    yield from self._iter_records(item, next_scope)

    def _looks_like_record(self, value: dict[str, Any]) -> bool:
        record_keys = {
            "content",
            "text",
            "summary",
            "topic",
            "memory",
            "value",
            "note",
            "title",
            "memory_id",
            "id",
            "importance",
            "confidence",
            "created_at",
            "updated_at",
        }
        return bool(record_keys & set(value.keys()))

    def _memory_store_entries(self, value: Any, limit: int = 20) -> list[dict[str, Any]]:
        entries = []
        for scope, item in self._iter_records(value):
            content = item.get("content") or item.get("text") or item.get("memory") or item.get("value") or item.get("summary")
            if not content and not self._looks_like_record(item):
                continue
            entries.append(
                {
                    "scope": self._redact_identifier(item.get("scope") or item.get("scope_id") or scope),
                    "memory_id": self._redact_identifier(item.get("memory_id") or item.get("id") or item.get("uid")),
                    "type": self._clean_scalar(item.get("type") or item.get("kind") or item.get("category"), limit=80),
                    "content": self._truncate_text(self._render_content_item(content), 500),
                    "importance": item.get("importance", ""),
                    "confidence": item.get("confidence", ""),
                    "created_at": self._clean_scalar(item.get("created_at") or item.get("created"), limit=80),
                    "updated_at": self._clean_scalar(item.get("updated_at") or item.get("last_updated_at") or item.get("last_seen_at"), limit=80),
                    "source": self._clean_scalar(item.get("source"), limit=120),
                    "tags": self._content_preview(item.get("tags"), max_items=8, max_chars=80),
                    "search_text": self._truncate_text(" ".join(str(item.get(key, "")) for key in ("type", "kind", "category", "content", "text", "memory", "summary", "tags")), 1000),
                }
            )
        entries.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
        return entries[:limit]

    def _summary_entries(self, value: Any, limit: int = 20) -> list[dict[str, Any]]:
        entries = []
        for scope, item in self._iter_records(value):
            summary = item.get("summary") or item.get("text") or item.get("content")
            entries.append(
                {
                    "scope": self._redact_identifier(item.get("scope") or item.get("scope_id") or scope),
                    "summary": self._truncate_text(self._render_content_item(summary), 500),
                    "created_at": self._clean_scalar(item.get("created_at"), limit=80),
                    "updated_at": self._clean_scalar(item.get("updated_at") or item.get("last_updated_at"), limit=80),
                    "expires_at": self._clean_scalar(item.get("expires_at") or item.get("expire_at"), limit=80),
                    "turn_count": item.get("turn_count") or item.get("turns") or "",
                    "search_text": self._truncate_text(self._render_content_item(summary), 1000),
                }
            )
        entries.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
        return entries[:limit]

    def _recent_trace_entries(self, value: Any, limit: int = 20) -> list[dict[str, Any]]:
        entries = []
        for scope, item in self._iter_records(value):
            topic = item.get("topic") or item.get("text") or item.get("content") or item.get("trace") or item.get("summary")
            entries.append(
                {
                    "scope": self._redact_identifier(item.get("scope") or item.get("scope_id") or scope),
                    "topic": self._truncate_text(self._render_content_item(topic), 500),
                    "importance": item.get("importance", ""),
                    "last_seen_at": self._clean_scalar(item.get("last_seen_at") or item.get("updated_at"), limit=80),
                    "expires_at": self._clean_scalar(item.get("expires_at") or item.get("expire_at"), limit=80),
                    "source": self._clean_scalar(item.get("source"), limit=120),
                    "search_text": self._truncate_text(self._render_content_item(topic), 1000),
                }
            )
        entries.sort(key=lambda item: item.get("last_seen_at") or "", reverse=True)
        return entries[:limit]

    def _flashback_state_preview(self, value: Any, config: dict[str, Any]) -> dict[str, Any]:
        return {
            "today_flashback_count": self._extract_first(value, ("today_flashback_count", "daily_count", "count_today", "today_count")),
            "same_memory_min_hours": config.get("same_memory_min_hours"),
            "max_flashback_per_day": config.get("max_flashback_per_day"),
            "flashback_min_turn_gap": config.get("flashback_min_turn_gap"),
            "last_flashbacks": self._structured_preview(value, max_items=30),
        }

    def _extract_first(self, value: Any, keys: tuple[str, ...]) -> Any:
        if isinstance(value, dict):
            for key in keys:
                if key in value:
                    return value[key]
            for item in value.values():
                found = self._extract_first(item, keys)
                if found not in ("", None):
                    return found
        return ""

    def _shared_config(self) -> dict[str, Any]:
        runtime = self._runtime_config_for(SHARED_PLUGIN_NAME)
        file_config = self._read_json_first(self._config_candidates(SHARED_PLUGIN_NAME), {})
        return self._merge_config(file_config, runtime)

    def _bridge_config(self) -> dict[str, Any]:
        runtime = self._runtime_config_for(BRIDGE_PLUGIN_NAME)
        file_config = self._read_json_first(self._config_candidates(BRIDGE_PLUGIN_NAME), {})
        defaults = {
            "enabled": False,
            "timezone": "Asia/Shanghai",
            "check_interval_minutes": 17,
            "post_windows": DEFAULT_WINDOWS,
            "max_posts_per_day": 1,
            "min_hours_between_posts": 8,
            "dry_run": True,
            "fallback_cookie": "",
        }
        return self._merge_config(defaults, file_config, runtime)

    def _runtime_config_for(self, plugin_name: str) -> dict[str, Any]:
        best: dict[str, Any] = {}
        for obj in self._iter_context_objects(limit=500):
            identity = self._identity(obj)
            if plugin_name not in identity:
                continue
            cfg = getattr(obj, "config", None) if self._has_attr(obj, "config") else None
            if isinstance(cfg, dict):
                best.update(cfg)
            elif cfg is not None:
                with contextlib.suppress(Exception):
                    for key in dir(cfg):
                        if key.startswith("_"):
                            continue
                        value = getattr(cfg, key)
                        if isinstance(value, (str, int, float, bool, list, dict, type(None))):
                            best[key] = value
        return best

    def _qzone_health(self, bridge_config: dict[str, Any]) -> dict[str, Any]:
        cookie = ""
        found = False
        for obj in self._iter_context_objects(limit=500):
            identity = self._identity(obj)
            if not any(name in identity for name in QZONE_PLUGIN_NAMES) and "qzone" not in identity:
                continue
            found = True
            cfg = getattr(obj, "config", None) if self._has_attr(obj, "config") else None
            cookie = cookie or self._first_mapping_value(cfg, ("cookie", "qzone_cookie"))
            cookie = cookie or self._first_object_value(obj, ("cookie", "_cookie", "qzone_cookie", "cookie_str"))
            fetcher = getattr(obj, "cookie_fetcher", None) if self._has_attr(obj, "cookie_fetcher") else None
            cookie = cookie or self._first_object_value(fetcher, ("cookie", "_cookie", "current_cookie", "last_cookie"))
            if cookie:
                break
        file_config = self._read_json_first(self._qzone_auto_like_config_candidates(), {})
        if isinstance(file_config, dict):
            found = found or bool(file_config)
            cookie = cookie or self._first_mapping_value(file_config, ("cookie", "qzone_cookie"))
        fallback_cookie = str(bridge_config.get("fallback_cookie") or "")
        configured = bool(cookie)
        fallback_configured = bool(fallback_cookie)
        selected = cookie or fallback_cookie
        return {
            "found": found,
            "cookie_configured": configured,
            "cookie_has_p_skey": "p_skey=" in selected,
            "cookie_len": len(selected),
            "fallback_cookie_configured": fallback_configured,
            "will_use_send_path": bool(found and selected),
            "last_error": "",
        }

    def _prediction(self, config: dict[str, Any], state: dict[str, Any], windows: list[dict[str, Any]]) -> dict[str, Any]:
        now = self._now()
        current = self._current_window(windows, now)
        next_window = self._next_window(windows, now)
        max_posts = int(config.get("max_posts_per_day", 1) or 1)
        today_count = int(state.get("today_post_count", 0) or 0)
        last_post_at = self._parse_datetime(state.get("last_post_at"))
        min_hours = int(config.get("min_hours_between_posts", 8) or 8)
        cooldown_ok = True
        cooldown_until = ""
        if last_post_at is not None:
            until = last_post_at + timedelta(hours=min_hours)
            cooldown_ok = now >= until
            cooldown_until = until.isoformat()
        quota_ok = today_count < max_posts
        in_window = current is not None
        probability = float(current.get("probability", 0) if current else 0)
        if not in_window or not quota_ok or not cooldown_ok:
            score = "Low"
        elif probability >= 0.35:
            score = "High"
        elif probability >= 0.15:
            score = "Medium"
        else:
            score = "Low"
        next_check_eta_minutes = self._next_check_eta_minutes(state.get("last_check_at"), config)
        return {
            "in_post_window": in_window,
            "current_window": current,
            "current_window_probability": probability,
            "next_check_in_minutes_approx": next_check_eta_minutes,
            "next_check_eta_minutes": next_check_eta_minutes,
            "quota_available_today": quota_ok,
            "cooldown_satisfied": cooldown_ok,
            "cooldown_until": cooldown_until,
            "next_window": next_window,
            "chance_score": score,
            "explanation": "概率窗口只表示命中概率，不表示一定触发。",
        }

    def _next_check_eta_minutes(self, last_check_at: Any, config: dict[str, Any]) -> int | None:
        last_check = self._parse_datetime(last_check_at)
        if last_check is None:
            return None
        interval_minutes = max(1, int(config.get("check_interval_minutes", 17) or 17))
        next_check_at = last_check + timedelta(minutes=interval_minutes)
        remaining_seconds = (next_check_at - self._now()).total_seconds()
        if remaining_seconds <= 0:
            return 0
        return int(math.ceil(remaining_seconds / 60))

    def _period_stale(self, period: dict[str, Any], expected_key: str, period_times: dict[str, str]) -> dict[str, Any]:
        last_key = str(period.get("last_period_key") or "")
        current_period = str(period.get("current_period") or "")
        labels = PERIOD_LABELS.get(expected_key, (expected_key,))
        conflicts_label = current_period and not any(label in current_period for label in labels)
        last_refresh = self._parse_datetime(period.get("last_period_refresh_at"))
        now = self._now()
        overdue = False
        if last_refresh is None:
            overdue = True
        else:
            overdue = now - last_refresh > timedelta(hours=8)
        slot_mismatch = bool(last_key and last_key != expected_key)
        stale = bool(overdue or slot_mismatch or conflicts_label)
        reason = []
        if overdue:
            reason.append("period 超过阈值没刷新")
        if slot_mismatch:
            reason.append(f"last_period_key={last_key}, 当前应为 {expected_key}")
        if conflicts_label:
            reason.append(f"current_period 与真实时间段 {expected_key} 明显冲突")
        return {"stale": stale, "expected_period_key": expected_key, "message": "；".join(reason)}

    def _history(self, history: Any, limit: int) -> list[dict[str, str]]:
        if not isinstance(history, list):
            return []
        rows = []
        for item in history[-limit:][::-1]:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "at": self._clean_scalar(item.get("at") or item.get("time") or item.get("created_at")),
                    "kind": self._clean_scalar(item.get("kind") or item.get("reason")),
                    "result": self._clean_scalar(item.get("result") or item.get("status")),
                    "window": self._clean_scalar(item.get("window")),
                    "text": self._clean_scalar(item.get("text") or item.get("generated_text")),
                    "detail": self._clean_scalar(item.get("detail") or item.get("error")),
                }
            )
        return rows

    def _today_check_timeline(self, history: Any, limit: int) -> list[dict[str, str]]:
        if not isinstance(history, list):
            return []
        today = self._now().date()
        rows: list[dict[str, str]] = []
        for item in history[::-1]:
            if not isinstance(item, dict):
                continue
            at = self._parse_datetime(item.get("at") or item.get("time") or item.get("created_at"))
            if at is None or at.date() != today:
                continue
            rows.append(
                {
                    "at": self._clean_scalar(item.get("at") or item.get("time") or item.get("created_at")),
                    "type": self._classify_check_event(item),
                    "window": self._clean_scalar(item.get("window")),
                    "result": self._clean_scalar(item.get("result") or item.get("status")),
                    "detail": self._clean_scalar(item.get("detail") or item.get("error")),
                }
            )
            if len(rows) >= limit:
                break
        return rows

    def _classify_check_event(self, item: dict[str, Any]) -> str:
        result = str(item.get("result") or item.get("status") or "").lower()
        detail = str(item.get("detail") or item.get("error") or "").lower()
        text = str(item.get("text") or item.get("generated_text") or "")
        combined = f"{result} {detail}"
        if any(marker in combined for marker in ("daily limit", "max_posts", "quota", "limit reached", "额度", "上限")):
            return "check_skip_quota"
        if any(marker in combined for marker in ("cooldown", "min_hours", "between posts", "冷却", "间隔", "上次发送", "最早")):
            return "check_skip_cooldown"
        if any(marker in combined for marker in ("probability", "draw=", "draw skipped", "抽签", "概率")):
            return "check_probability_miss"
        if result in ("sent", "dry_run", "generated", "send_failed") or text:
            return "check_hit"
        return "check_pass"

    def _extract_recent_traces(self, memory_data: Any) -> list[Any]:
        if not isinstance(memory_data, dict):
            return []
        for key in ("recent_traces", "traces", "memory_traces"):
            value = memory_data.get(key)
            if isinstance(value, list):
                return [self._trace_public(item) for item in value][-5:][::-1]
        days = memory_data.get("days")
        traces = []
        if isinstance(days, dict):
            for day, payload in days.items():
                if isinstance(payload, dict):
                    values = payload.get("recent_traces") or payload.get("traces") or []
                    if isinstance(values, list):
                        traces.extend(values)
        return [self._trace_public(item) for item in traces][-5:][::-1]

    def _extract_days(self, memory_data: Any) -> list[str]:
        if not isinstance(memory_data, dict):
            return []
        days = memory_data.get("days")
        if isinstance(days, dict):
            return sorted(str(key) for key in days.keys())[-7:][::-1]
        if isinstance(days, list):
            return [str(item) for item in days[-7:]][::-1]
        return []

    def _repeat_rate_24h(self, memory_data: Any) -> float:
        traces = self._all_trace_texts(memory_data)
        if len(traces) < 2:
            return 0.0
        normalized = [self._normalize_trace_text(item) for item in traces if self._normalize_trace_text(item)]
        if len(normalized) < 2:
            return 0.0
        unique_count = len(set(normalized))
        return round(max(0.0, 1.0 - unique_count / len(normalized)), 3)

    def _all_trace_texts(self, memory_data: Any) -> list[str]:
        traces = []
        if isinstance(memory_data, dict):
            for key in ("recent_traces", "traces", "memory_traces"):
                if isinstance(memory_data.get(key), list):
                    traces.extend(memory_data[key])
            days = memory_data.get("days")
            if isinstance(days, dict):
                for payload in days.values():
                    if isinstance(payload, dict):
                        for key in ("recent_traces", "traces", "memory_traces"):
                            if isinstance(payload.get(key), list):
                                traces.extend(payload[key])
        cutoff = self._now() - timedelta(hours=24)
        timed_items = []
        untimed_items = []
        for item in traces:
            at = self._trace_at(item)
            if at is None:
                untimed_items.append(item)
            elif at >= cutoff:
                timed_items.append(item)
        selected = timed_items if timed_items else traces[-50:]
        return [self._trace_text(item) for item in selected]

    def _trace_public(self, item: Any) -> Any:
        if isinstance(item, dict):
            return {key: self._clean_scalar(item.get(key)) for key in ("at", "date", "trace", "text", "micro_experience") if key in item}
        return self._clean_scalar(item)

    def _trace_text(self, item: Any) -> str:
        if isinstance(item, dict):
            for key in ("trace", "text", "micro_experience", "value"):
                if item.get(key):
                    return str(item.get(key))
        return str(item or "")

    def _trace_at(self, item: Any) -> datetime | None:
        if not isinstance(item, dict):
            return None
        for key in ("at", "time", "created_at", "updated_at", "date"):
            parsed = self._parse_datetime(item.get(key))
            if parsed is not None:
                return parsed
        return None

    def _normalize_trace_text(self, text: str) -> str:
        return re.sub(r"\s+", "", str(text).strip().lower())

    def _consecutive_errors(self, history: list[dict[str, Any]], last_error: Any) -> int:
        count = 1 if last_error else 0
        for item in history:
            text = " ".join(str(item.get(key, "")) for key in ("result", "detail", "kind")).lower()
            if any(marker in text for marker in ("error", "failed", "fail", "失败", "错误", "异常")):
                count += 1
            else:
                break
        return count

    def _resolve_data_root(self) -> Path | None:
        try:
            return Path(get_astrbot_data_path())
        except Exception:
            return None

    def _candidate_data_roots(self) -> list[Path]:
        roots: list[Path] = []
        if self.data_root is not None:
            roots.append(self.data_root)
        cwd = Path.cwd()
        roots.extend(
            [
                Path("/AstrBot/data"),
                Path("./data"),
                cwd / "data",
                self.plugin_dir.parent / "data",
                self.plugin_dir.parent.parent,
                self.plugin_dir.parent.parent / "data",
                self.plugin_dir.parent.parent.parent / "data",
            ]
        )
        result: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            with contextlib.suppress(Exception):
                resolved = root.resolve()
                key = str(resolved).lower()
                if key not in seen:
                    seen.add(key)
                    result.append(resolved)
        return result

    def _candidate_plugin_dirs(self, plugin_name: str) -> list[Path]:
        dirs: list[Path] = []
        for root in self._candidate_data_roots():
            dirs.append(root / "plugin_data" / plugin_name)
            dirs.append(root / "plugins" / plugin_name)
        dirs.extend(
            [
                self.plugin_dir.parent / plugin_name,
                self.plugin_dir.parent / "plugins" / plugin_name,
                self.plugin_dir.parent.parent / "plugins" / plugin_name,
                self.plugin_dir.parent.parent / "data" / "plugins" / plugin_name,
            ]
        )
        result: list[Path] = []
        seen: set[str] = set()
        for directory in dirs:
            with contextlib.suppress(Exception):
                resolved = directory.resolve()
                key = str(resolved).lower()
                if key not in seen:
                    seen.add(key)
                    result.append(resolved)
        return result

    def _shared_life_candidates(self, filename: str) -> list[Path]:
        paths: list[Path] = []
        for directory in self._candidate_plugin_dirs(SHARED_PLUGIN_NAME):
            paths.append(directory / filename)
            paths.append(directory / "data" / filename)
        paths.append(self.plugin_dir / "data" / filename)
        return self._dedupe_paths(paths)

    def _bridge_state_candidates(self) -> list[Path]:
        filename = "qzone_life_bridge_state.json"
        paths: list[Path] = []
        for directory in self._candidate_plugin_dirs(BRIDGE_PLUGIN_NAME):
            paths.append(directory / filename)
            paths.append(directory / "data" / filename)
        paths.append(self.plugin_dir / "data" / filename)
        return self._dedupe_paths(paths)

    def _config_candidates(self, plugin_name: str) -> list[Path]:
        paths: list[Path] = []
        for root in self._candidate_data_roots():
            paths.append(root / "config" / f"{plugin_name}_config.json")
            paths.append(root / "config" / f"{plugin_name}.json")
        for directory in self._candidate_plugin_dirs(plugin_name):
            paths.append(directory / "config.json")
            paths.append(directory / f"{plugin_name}_config.json")
        return self._dedupe_paths(paths)

    def _dedupe_paths(self, paths: list[Path]) -> list[Path]:
        result: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(path)
        return result

    def _qzone_auto_like_config_candidates(self) -> list[Path]:
        paths: list[Path] = []
        for name in QZONE_PLUGIN_NAMES:
            paths.extend(self._config_candidates(name))
        return paths

    def _read_json_first(self, candidates: list[Path], default: Any) -> Any:
        for path in candidates:
            if not path.exists() or not path.is_file():
                continue
            with contextlib.suppress(Exception):
                return json.loads(path.read_text(encoding="utf-8-sig"))
        return default

    def _first_existing(self, candidates: list[Path]) -> Path | None:
        for path in candidates:
            if path.exists() and path.is_file():
                return path
        return None

    def _merge_config(self, *configs: Any) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for config in configs:
            if isinstance(config, dict):
                merged.update(config)
        return merged

    def _iter_context_objects(self, limit: int = 1000) -> list[Any]:
        if self.context is None:
            return []
        roots = [self.context]
        seen: set[int] = set()
        result: list[Any] = []
        stack: list[tuple[Any, int]] = [(root, 0) for root in roots]
        container_attrs = (
            "plugin_manager",
            "star_manager",
            "plugins",
            "_plugins",
            "plugin_map",
            "_plugin_map",
            "stars",
            "_stars",
            "star_map",
            "_star_map",
            "loaded_plugins",
            "_loaded_plugins",
            "instances",
            "_instances",
        )
        while stack and len(result) < limit:
            obj, depth = stack.pop()
            if obj is None or isinstance(obj, (str, bytes, int, float, bool, Path)):
                continue
            oid = id(obj)
            if oid in seen:
                continue
            seen.add(oid)
            result.append(obj)
            if depth >= 6:
                continue
            children: list[Any] = []
            if isinstance(obj, dict):
                children.extend(obj.values())
            elif isinstance(obj, (list, tuple, set)):
                children.extend(obj)
            else:
                for attr in container_attrs:
                    with contextlib.suppress(Exception):
                        value = getattr(obj, attr)
                        if value is not None:
                            children.append(value)
                with contextlib.suppress(Exception):
                    for value in vars(obj).values():
                        if value is not None:
                            children.append(value)
            for child in children:
                stack.append((child, depth + 1))
        return result

    def _identity(self, obj: Any) -> str:
        parts = [obj.__class__.__name__, getattr(obj.__class__, "__module__", "")]
        for attr in ("name", "plugin_name", "plugin_id", "id"):
            with contextlib.suppress(Exception):
                parts.append(str(getattr(obj, attr, "")))
        with contextlib.suppress(Exception):
            metadata = getattr(obj, "metadata", None)
            if isinstance(metadata, dict):
                parts.extend(str(metadata.get(key, "")) for key in ("name", "id", "plugin_name"))
        return " ".join(parts).lower()

    def _has_attr(self, obj: Any, attr: str) -> bool:
        try:
            getattr(obj, attr)
            return True
        except Exception:
            return False

    def _first_mapping_value(self, obj: Any, keys: tuple[str, ...]) -> str:
        if not isinstance(obj, dict):
            return ""
        for key in keys:
            value = obj.get(key)
            if value:
                return str(value)
        return ""

    def _first_object_value(self, obj: Any, attrs: tuple[str, ...]) -> str:
        if obj is None:
            return ""
        for attr in attrs:
            with contextlib.suppress(Exception):
                value = getattr(obj, attr)
                if value:
                    return str(value)
        return ""

    def _parse_windows(self, value: Any) -> list[dict[str, Any]]:
        raw = value
        if isinstance(raw, str):
            with contextlib.suppress(Exception):
                raw = json.loads(raw)
        if not isinstance(raw, list):
            raw = DEFAULT_WINDOWS
        windows = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = self._clean_scalar(item.get("name", "window"))
            start = self._clean_scalar(item.get("start"))
            end = self._clean_scalar(item.get("end"))
            probability = self._parse_probability(item.get("probability"))
            if start and end:
                windows.append({"name": name, "start": start, "end": end, "probability": probability})
        return windows or DEFAULT_WINDOWS

    def _parse_period_times(self, value: Any) -> dict[str, str]:
        raw = value
        if isinstance(raw, str):
            with contextlib.suppress(Exception):
                raw = json.loads(raw)
        if not isinstance(raw, dict):
            raw = DEFAULT_PERIOD_REFRESH_TIMES
        result = dict(DEFAULT_PERIOD_REFRESH_TIMES)
        for key in DEFAULT_PERIOD_REFRESH_TIMES:
            text = str(raw.get(key, result[key])).strip()
            if re.fullmatch(r"\d{1,2}:\d{2}", text):
                result[key] = text
        return result

    def _current_window(self, windows: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
        now_minutes = now.hour * 60 + now.minute
        for window in windows:
            start = self._hhmm_minutes(window.get("start"))
            end = self._hhmm_minutes(window.get("end"))
            if start is None or end is None:
                continue
            if start <= end:
                inside = start <= now_minutes < end
            else:
                inside = now_minutes >= start or now_minutes < end
            if inside:
                return window
        return None

    def _next_window(self, windows: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
        candidates = []
        for window in windows:
            start = self._hhmm_minutes(window.get("start"))
            if start is None:
                continue
            candidate = now.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            candidates.append((candidate, window))
        if not candidates:
            return None
        candidate, window = min(candidates, key=lambda item: item[0])
        public = dict(window)
        public["starts_at"] = candidate.isoformat()
        public["minutes_until"] = max(0, math.ceil((candidate - now).total_seconds() / 60))
        return public

    def _next_daily_refresh(self, config: dict[str, Any]) -> str:
        target = str(config.get("auto_refresh_time", "04:00") or "04:00")
        minutes = self._hhmm_minutes(target) or 240
        now = self._now()
        candidate = now.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.isoformat()

    def _next_period_refresh(self, period_times: dict[str, str]) -> str:
        now = self._now()
        candidates = []
        for key, value in period_times.items():
            minutes = self._hhmm_minutes(value)
            if minutes is None:
                continue
            candidate = now.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            candidates.append((candidate, key))
        if not candidates:
            return ""
        candidate, key = min(candidates, key=lambda item: item[0])
        return f"{candidate.isoformat()} ({key})"

    def _expected_period_key(self, period_times: dict[str, str]) -> str:
        now = self._now()
        now_minutes = now.hour * 60 + now.minute
        ordered = []
        for key, value in period_times.items():
            minutes = self._hhmm_minutes(value)
            if minutes is not None:
                ordered.append((minutes, key))
        if not ordered:
            return "night"
        ordered.sort()
        current = ordered[-1][1]
        for minutes, key in ordered:
            if now_minutes >= minutes:
                current = key
        return current

    def _parse_datetime(self, value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        with contextlib.suppress(Exception):
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=self._timezone())
            return parsed.astimezone(self._timezone())
        return None

    def _hhmm_minutes(self, value: Any) -> int | None:
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value or "").strip())
        if not match:
            return None
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 23 or minute > 59:
            return None
        return hour * 60 + minute

    def _parse_probability(self, value: Any) -> float:
        with contextlib.suppress(Exception):
            return max(0.0, min(1.0, float(value)))
        return 0.0

    def _current_activity(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return {
                "value": self._clean_scalar(value.get("value")),
                "mode": self._clean_scalar(value.get("mode")),
            }
        return {"value": self._clean_scalar(value), "mode": ""}

    def _clean_scalar(self, value: Any, limit: int = 800) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False)
        else:
            text = str(value)
        text = text.replace("\r", " ").strip()
        if len(text) > limit:
            text = text[:limit].rstrip() + "..."
        return text

    def _redact_recursive(self, value: Any, key: str = "") -> Any:
        if SENSITIVE_KEY_PATTERN.search(key):
            if key.lower() in SENSITIVE_PUBLIC_KEYS:
                return value
            return "<redacted>"
        if isinstance(value, dict):
            return {item_key: self._redact_recursive(item_value, item_key) for item_key, item_value in value.items()}
        if isinstance(value, list):
            return [self._redact_recursive(item, key) for item in value]
        if isinstance(value, str):
            return self._redact_cookie_text(value)
        return value

    def _redact_cookie_text(self, text: str) -> str:
        redacted = re.sub(
            r"(p_skey|skey|pt4_token|access_token|refresh_token|authorization|dashboard_password|password|token|secret|key)=([^;\s,}]+)",
            r"\1=<redacted>",
            text,
            flags=re.IGNORECASE,
        )
        redacted = re.sub(
            r"(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._~+/=-]+",
            r"\1<redacted>",
            redacted,
            flags=re.IGNORECASE,
        )
        redacted = re.sub(r"\d{7,}", lambda match: f"***{match.group(0)[-3:]}", redacted)
        if "uin=" in redacted and ";" in redacted and len(redacted) > 120:
            return "<cookie:redacted>"
        return redacted

    def _timezone(self):
        name = str(self.dashboard_config_provider().get("timezone", "Asia/Shanghai") or "Asia/Shanghai")
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            return dt_timezone(timedelta(hours=8), name="Asia/Shanghai")

    def _now(self) -> datetime:
        return datetime.now(self._timezone()).replace(microsecond=0)
