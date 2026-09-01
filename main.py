from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

try:
    from astrbot.api import AstrBotConfig, logger
    from astrbot.api.event import AstrMessageEvent, filter
    from astrbot.api.star import Context, Star, register
except ImportError:  # pragma: no cover - local syntax checks outside AstrBot.
    import logging

    AstrBotConfig = dict  # type: ignore[misc,assignment]
    logger = logging.getLogger("astrbot_plugin_aling_life_dashboard")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    class AstrMessageEvent:  # type: ignore[no-redef]
        message_str: str = ""

        @staticmethod
        def plain_result(text: str) -> str:
            return text

    class Context:  # type: ignore[no-redef]
        pass

    class Star:  # type: ignore[no-redef]
        def __init__(self, context: Context):
            self.context = context
            self.name = PLUGIN_NAME

    class _Filter:
        def command(self, _name: str, **_kwargs: Any):
            def _decorator(func):
                return func

            return _decorator

        def on_astrbot_loaded(self, **_kwargs: Any):
            def _decorator(func):
                return func

            return _decorator

    def register(*_args: Any, **_kwargs: Any):
        def _decorator(cls):
            return cls

        return _decorator

    filter = _Filter()  # type: ignore[assignment]

try:
    from .webui.data_reader import DashboardDataReader
    from .webui.server import LifeDashboardWebUI
except ImportError:  # pragma: no cover - fallback for file-based plugin loaders.
    from webui.data_reader import DashboardDataReader  # type: ignore[no-redef]
    from webui.server import LifeDashboardWebUI  # type: ignore[no-redef]


PLUGIN_NAME = "astrbot_plugin_aling_life_dashboard"
PLUGIN_VERSION = "0.1.1"
DEFAULT_CONFIG: dict[str, Any] = {
    "dashboard_enabled": False,
    "bind_host": "127.0.0.1",
    "bind_port": 7842,
    "dashboard_password": "",
    "refresh_interval_seconds": 10,
    "timezone": "Asia/Shanghai",
    "show_memory": True,
    "history_limit": 20,
    "port_conflict_retry": True,
    "health_cache_seconds": 5,
}

ON_ASTRBOT_LOADED = getattr(filter, "on_astrbot_loaded", lambda **_kwargs: (lambda func: func))
REGISTRY_ATTR = "_astrbot_plugin_aling_life_dashboard_webui"


def _plain(event: AstrMessageEvent, text: str) -> Any:
    plain_result = getattr(event, "plain_result", None)
    if callable(plain_result):
        return plain_result(text)
    return text


def _normalize_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on", "enabled"):
            return True
        if lowered in ("false", "0", "no", "off", "disabled"):
            return False
    if value is None:
        return default
    return bool(value)


def _parse_int(value: Any, default: int, minimum: int | None = None) -> int:
    try:
        result = int(value)
    except Exception:
        result = default
    if minimum is not None:
        result = max(minimum, result)
    return result


@register(
    PLUGIN_NAME,
    "Codex",
    "Read-only WebUI dashboard for shared_life_context and qzone_life_bridge.",
    PLUGIN_VERSION,
)
class AlingLifeDashboardPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.plugin_dir = Path(__file__).resolve().parent
        self._stop_previous_webui()
        self.reader = DashboardDataReader(self.plugin_dir, context=self.context, dashboard_config_provider=self._settings)
        self.webui = LifeDashboardWebUI(
            plugin_dir=self.plugin_dir,
            settings_provider=self._settings,
            data_reader=self.reader,
            logger=logger,
        )
        setattr(builtins, REGISTRY_ATTR, self.webui)
        self._start_from_config()

    @ON_ASTRBOT_LOADED()
    async def on_astrbot_loaded(self):
        self._start_from_config()

    @filter.command("ald")
    async def ald(self, event: AstrMessageEvent):
        raw_message = _normalize_text(getattr(event, "message_str", ""))
        parts = self._strip_prefix(raw_message).split(maxsplit=1)
        action = parts[0].lower() if parts else "status"

        if action == "start":
            return _plain(event, self._start_command())
        if action == "stop":
            await self._stop_command()
            return _plain(event, f"aling_life_dashboard WebUI state: {self.webui.state_text}")
        if action == "url":
            return _plain(event, self._render_url())
        if action == "status":
            return _plain(event, self._render_status())
        return _plain(event, self._help_text())

    async def terminate(self):
        try:
            stopped = await self.webui.stop()
            if not stopped:
                logger.warning("aling_life_dashboard terminate did not fully stop WebUI")
        except Exception as exc:
            logger.warning("aling_life_dashboard terminate stop failed: %s", exc)
        finally:
            if getattr(builtins, REGISTRY_ATTR, None) is self.webui:
                setattr(builtins, REGISTRY_ATTR, None)

    def _settings(self) -> dict[str, Any]:
        settings = dict(DEFAULT_CONFIG)
        for key, default in DEFAULT_CONFIG.items():
            value = self._config_get(key, default)
            settings[key] = default if value is None else value

        settings["dashboard_enabled"] = _parse_bool(settings.get("dashboard_enabled"), False)
        settings["bind_host"] = _normalize_text(settings.get("bind_host"), "127.0.0.1")
        settings["bind_port"] = _parse_int(settings.get("bind_port"), 7842, minimum=1)
        settings["dashboard_password"] = _normalize_text(settings.get("dashboard_password"))
        settings["refresh_interval_seconds"] = _parse_int(settings.get("refresh_interval_seconds"), 10, minimum=3)
        settings["timezone"] = _normalize_text(settings.get("timezone"), "Asia/Shanghai")
        settings["show_memory"] = _parse_bool(settings.get("show_memory"), True)
        settings["history_limit"] = _parse_int(settings.get("history_limit"), 20, minimum=1)
        settings["port_conflict_retry"] = _parse_bool(settings.get("port_conflict_retry"), True)
        settings["health_cache_seconds"] = _parse_int(settings.get("health_cache_seconds"), 5, minimum=1)
        return settings

    def _config_get(self, key: str, default: Any = None) -> Any:
        try:
            if hasattr(self.config, "get"):
                return self.config.get(key, default)
        except Exception:
            pass
        try:
            return getattr(self.config, key)
        except Exception:
            return default

    def _start_from_config(self) -> None:
        settings = self._settings()
        if not settings["dashboard_enabled"]:
            if not self.webui.stop_sync():
                logger.warning("aling_life_dashboard WebUI stop timed out while dashboard_enabled=false")
            logger.info("aling_life_dashboard WebUI not started: dashboard_enabled=false")
            return
        if not settings["dashboard_password"]:
            if not self.webui.stop_sync():
                logger.warning("aling_life_dashboard WebUI stop timed out while dashboard_password is empty")
            logger.warning("aling_life_dashboard WebUI not started: dashboard_password is empty")
            return
        self.webui.start()

    def _stop_previous_webui(self) -> None:
        previous = getattr(builtins, REGISTRY_ATTR, None)
        if previous is None:
            return
        stop_sync = getattr(previous, "stop_sync", None)
        if not callable(stop_sync):
            return
        try:
            stopped = stop_sync()
            if not stopped:
                logger.warning("aling_life_dashboard previous WebUI did not stop before reload")
            elif getattr(builtins, REGISTRY_ATTR, None) is previous:
                setattr(builtins, REGISTRY_ATTR, None)
        except Exception as exc:
            logger.warning("aling_life_dashboard failed to stop previous WebUI before reload: %s", exc)

    def _start_command(self) -> str:
        settings = self._settings()
        if not settings["dashboard_password"]:
            return "WebUI not started: dashboard_password is empty. Set a password in plugin config first."
        self.webui.start(force=True)
        if self.webui.running:
            return f"aling_life_dashboard WebUI started: {self.webui.local_url}"
        return f"WebUI is starting or failed. Check logs. state={self.webui.state_text}"

    async def _stop_command(self) -> None:
        stopped = await self.webui.stop()
        if not stopped:
            logger.warning("aling_life_dashboard /ald stop timed out")

    def _render_status(self) -> str:
        snapshot = self.reader.read_all()
        life = snapshot.get("life", {})
        qzone = snapshot.get("qzone", {})
        refresh = life.get("refresh", {})
        bridge = qzone.get("bridge", {})
        url = self.webui.local_url if self.webui.running else "not started"
        return "\n".join(
            [
                "aling_life_dashboard status:",
                f"current_period: {life.get('period', {}).get('current_period', '-')}",
                f"micro_experience: {life.get('period', {}).get('micro_experience', '-')}",
                f"next_period_refresh: {refresh.get('next_period_refresh_at', '-')}",
                f"today_post_count: {bridge.get('today_post_count', 0)}",
                f"last_post_at: {bridge.get('last_post_at') or '-'}",
                f"last_error: {bridge.get('last_error') or '-'}",
                f"WebUI URL: {url}",
            ]
        )

    def _render_url(self) -> str:
        settings = self._settings()
        port = self.webui.bound_port or settings["bind_port"]
        if settings["bind_host"] == "127.0.0.1":
            return "\n".join(
                [
                    "WebUI is bound to 127.0.0.1. Use an SSH tunnel:",
                    f"ssh -L {port}:127.0.0.1:{port} root@SERVER_IP",
                    f"Then open: http://127.0.0.1:{port}",
                ]
            )
        if settings["bind_host"] == "0.0.0.0":
            return "\n".join(
                [
                    f"http://SERVER_IP:{port}",
                    "Public bind is enabled. Restrict cloud security-group source IPs and use a strong dashboard_password.",
                ]
            )
        return f"http://{settings['bind_host']}:{port}"

    def _help_text(self) -> str:
        return "\n".join(["/ald status", "/ald url", "/ald start", "/ald stop"])

    def _strip_prefix(self, message: str) -> str:
        stripped = message.strip()
        for prefix in ("/ald", "ald"):
            if stripped.lower().startswith(prefix):
                return stripped[len(prefix) :].strip()
        return stripped
