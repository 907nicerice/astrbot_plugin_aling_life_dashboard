from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
from pathlib import Path
from typing import Any

from hypercorn.asyncio import serve
from hypercorn.config import Config
from quart import Quart, jsonify, redirect, request, send_from_directory

from .auth import DashboardAuth, clear_login_cookie, login_redirect, set_login_cookie


class LifeDashboardWebUI:
    def __init__(self, plugin_dir: Path, settings_provider, data_reader, logger):
        self.plugin_dir = Path(plugin_dir)
        self.settings_provider = settings_provider
        self.data_reader = data_reader
        self.logger = logger
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._lock = threading.Lock()
        self.bound_host = ""
        self.bound_port = 0
        self.state_text = "stopped"

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self.state_text == "running"

    @property
    def local_url(self) -> str:
        port = self.bound_port or int(self.settings_provider().get("bind_port", 7842))
        host = self.settings_provider().get("bind_host", "127.0.0.1")
        display_host = "服务器IP" if host == "0.0.0.0" else host
        return f"http://{display_host}:{port}"

    def start(self, force: bool = False) -> None:
        settings = self.settings_provider()
        if not force and not settings.get("dashboard_enabled", False):
            self.state_text = "disabled"
            return
        if not settings.get("dashboard_password"):
            self.state_text = "missing_password"
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self.state_text = "starting"
            self._thread = threading.Thread(target=self._thread_main, name="aling-life-dashboard-webui", daemon=True)
            self._thread.start()

    async def stop(self, timeout: float = 8.0) -> bool:
        thread = self._thread
        loop = self._loop
        event = self._shutdown_event
        if thread is None or not thread.is_alive():
            self._mark_stopped()
            return True
        if loop is not None and event is not None:
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(event.set)
        try:
            await asyncio.to_thread(thread.join, timeout)
        except Exception as exc:
            self.logger.warning("aling_life_dashboard WebUI stop join failed: %s", exc)
        if thread.is_alive():
            self.state_text = "stop_timeout"
            self.logger.warning("aling_life_dashboard WebUI stop timed out; thread is still alive on port %s", self.bound_port)
            return False
        self._mark_stopped()
        return True

    def stop_sync(self, timeout: float = 8.0) -> bool:
        thread = self._thread
        loop = self._loop
        event = self._shutdown_event
        if thread is None or not thread.is_alive():
            self._mark_stopped()
            return True
        if loop is not None and event is not None:
            with contextlib.suppress(Exception):
                loop.call_soon_threadsafe(event.set)
        try:
            deadline = time.monotonic() + max(0.1, timeout)
            while thread.is_alive() and time.monotonic() < deadline:
                thread.join(timeout=0.2)
        except Exception as exc:
            self.logger.warning("aling_life_dashboard WebUI stop_sync join failed: %s", exc)
        if thread.is_alive():
            self.state_text = "stop_timeout"
            self.logger.warning("aling_life_dashboard WebUI stop_sync timed out; thread is still alive on port %s", self.bound_port)
            return False
        self._mark_stopped()
        return True

    def _mark_stopped(self) -> None:
        self.state_text = "stopped"
        self._thread = None
        self._loop = None
        self._shutdown_event = None
        self.bound_port = 0
        self.bound_host = ""

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._shutdown_event = asyncio.Event()
        try:
            loop.run_until_complete(self._serve_with_retry())
        except Exception:
            self.state_text = "failed"
            self.logger.exception("aling_life_dashboard WebUI crashed")
        finally:
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            if self.state_text != "failed":
                self.state_text = "stopped"

    async def _serve_with_retry(self) -> None:
        settings = self.settings_provider()
        host = str(settings.get("bind_host", "127.0.0.1"))
        base_port = int(settings.get("bind_port", 7842))
        max_offset = 5 if bool(settings.get("port_conflict_retry", True)) else 0
        last_error: Exception | None = None

        for offset in range(max_offset + 1):
            port = base_port + offset
            if not self._port_available(host, port):
                last_error = OSError(f"port {port} is already in use")
                continue
            app = self._create_app()
            config = Config()
            config.bind = [f"{host}:{port}"]
            config.accesslog = None
            config.errorlog = None
            config.use_reloader = False
            config.graceful_timeout = 2
            config.shutdown_timeout = 2
            self.bound_host = host
            self.bound_port = port
            self.state_text = "running"
            self.logger.info("aling_life_dashboard WebUI listening on %s:%s", host, port)
            await serve(app, config, shutdown_trigger=self._shutdown_event.wait)  # type: ignore[union-attr]
            return

        self.state_text = "failed"
        raise last_error or OSError("no available dashboard port")

    def _port_available(self, host: str, port: int) -> bool:
        probe_host = "0.0.0.0" if host == "0.0.0.0" else host
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((probe_host, port))
            return True
        except OSError:
            return False
        finally:
            sock.close()

    def _create_app(self) -> Quart:
        static_dir = self.plugin_dir / "static"
        app = Quart(__name__, static_folder=None)
        auth = DashboardAuth(self.settings_provider)

        @app.get("/")
        async def index():
            if not auth.is_authorized_request(request):
                return login_redirect()
            return await send_from_directory(static_dir, "dashboard.html")

        @app.get("/login")
        async def login_page():
            if auth.is_authorized_request(request):
                return redirect("/")
            return _login_html()

        @app.post("/login")
        async def login_post():
            password = ""
            if request.is_json:
                payload = await request.get_json(silent=True) or {}
                password = str(payload.get("password", ""))
            else:
                form = await request.form
                password = str(form.get("password", ""))
            token = auth.login(password)
            if not token:
                self.logger.warning("aling_life_dashboard login failed from %s", request.remote_addr or "unknown")
                if request.is_json:
                    return jsonify({"ok": False, "error": "invalid_password"}), 401
                return _login_html(error=True), 401
            response = redirect("/")
            return set_login_cookie(response, token)

        @app.post("/logout")
        async def logout_post():
            token = auth.current_token(request)
            auth.logout(token)
            response = redirect("/login")
            return clear_login_cookie(response)

        @app.get("/logout")
        async def logout_get():
            token = auth.current_token(request)
            auth.logout(token)
            response = redirect("/login")
            return clear_login_cookie(response)

        @app.get("/static/<path:filename>")
        async def static_files(filename: str):
            if not auth.is_authorized_request(request):
                return login_redirect()
            return await send_from_directory(static_dir, filename)

        async def _api_guard():
            if not auth.is_authorized_request(request):
                return jsonify({"error": "unauthorized"}), 401
            return None

        @app.get("/api/status")
        async def api_status():
            guard = await _api_guard()
            if guard:
                return guard
            return jsonify(self._safe_snapshot_section("status"))

        @app.get("/api/life")
        async def api_life():
            guard = await _api_guard()
            if guard:
                return guard
            return jsonify(self._safe_snapshot_section("life"))

        @app.get("/api/qzone")
        async def api_qzone():
            guard = await _api_guard()
            if guard:
                return guard
            return jsonify(self._safe_snapshot_section("qzone"))

        @app.get("/api/memory")
        async def api_memory():
            guard = await _api_guard()
            if guard:
                return guard
            return jsonify(self._safe_snapshot_section("memory"))

        @app.get("/api/health")
        async def api_health():
            guard = await _api_guard()
            if guard:
                return guard
            return jsonify(self._safe_snapshot_section("health"))

        @app.get("/api/continuity-content")
        async def api_continuity_content():
            guard = await _api_guard()
            if guard:
                return guard
            try:
                return jsonify(self.data_reader.read_continuity_content())
            except Exception as exc:
                self.logger.warning("aling_life_dashboard continuity content degraded: %s", exc)
                return jsonify({"ok": False, "degraded": True, "error": "read_failed"}), 200

        @app.get("/api/continuity-debug")
        async def api_continuity_debug():
            guard = await _api_guard()
            if guard:
                return guard
            try:
                return jsonify(self.data_reader.read_continuity_debug())
            except Exception as exc:
                self.logger.warning("aling_life_dashboard continuity debug degraded: %s", exc)
                return jsonify({"ok": False, "degraded": True, "error": "read_failed"}), 200

        return app

    def _safe_snapshot_section(self, section: str) -> dict[str, Any]:
        try:
            snapshot = self.data_reader.read_all()
            value = snapshot.get(section, {})
            return value if isinstance(value, dict) else {"degraded": True, "error": "invalid_section"}
        except Exception as exc:
            self.logger.warning("aling_life_dashboard API section %s degraded: %s", section, exc)
            return {"degraded": True, "error": "read_failed"}


def _login_html(error: bool = False) -> str:
    error_html = '<div class="error">密码不正确，请重新输入。</div>' if error else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Aling Life Dashboard Login</title>
  <style>
    :root {{
      color-scheme: dark;
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --bg: #11151b;
      --panel: #1b222b;
      --line: rgba(159, 176, 193, 0.22);
      --text: #f3f6f8;
      --muted: #a8b5c2;
      --accent: #ee8f7d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      padding: 24px;
      background:
        radial-gradient(circle at 18% 6%, rgba(238, 143, 125, 0.16), transparent 28rem),
        linear-gradient(180deg, #151a21, var(--bg));
      color: var(--text);
    }}
    form {{
      width: min(420px, 100%);
      display: grid;
      gap: 16px;
      padding: 30px;
      border: 1px solid var(--line);
      border-radius: 22px;
      background: linear-gradient(180deg, rgba(32, 41, 52, 0.88), rgba(27, 34, 43, 0.96));
      box-shadow: 0 18px 50px rgba(0, 0, 0, 0.24);
    }}
    h1 {{ margin: 0; font-size: 26px; line-height: 1.15; letter-spacing: 0; }}
    p {{ margin: -6px 0 4px; color: var(--muted); font-size: 14px; line-height: 1.6; }}
    input {{
      height: 46px;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 0 14px;
      background: rgba(17, 21, 27, 0.9);
      color: #fff;
      font-size: 15px;
      outline: none;
    }}
    input:focus {{
      border-color: rgba(238, 143, 125, 0.62);
      box-shadow: 0 0 0 3px rgba(238, 143, 125, 0.13);
    }}
    button {{
      height: 46px;
      border: 1px solid rgba(238, 143, 125, 0.5);
      border-radius: 14px;
      background: rgba(238, 143, 125, 0.18);
      color: #fff;
      font-weight: 760;
      cursor: pointer;
    }}
    button:hover {{ background: rgba(238, 143, 125, 0.26); }}
    .error {{
      border: 1px solid rgba(238, 129, 125, 0.38);
      border-radius: 12px;
      padding: 10px 12px;
      background: rgba(238, 129, 125, 0.13);
      color: #ffc1be;
      font-size: 14px;
    }}
  </style>
</head>
<body>
  <form method="post" action="/login">
    <h1>Aling Life Dashboard</h1>
    <p>输入 Dashboard 密码后查看只读状态面板。</p>
    {error_html}
    <input name="password" type="password" autocomplete="current-password" placeholder="Dashboard password" required autofocus>
    <button type="submit">进入 Dashboard</button>
  </form>
</body>
</html>"""
