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
from .memory_editor import MemoryEditor, MemoryEditorError
from .test_space import TestSpaceCleaner


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
        memory_editor = MemoryEditor(
            path_provider=self.data_reader.find_aling_memory_store_path,
            enabled_provider=lambda: bool(self.settings_provider().get("memory_edit_enabled", False)),
            invalidate_callback=self.data_reader.invalidate_cache,
        )
        test_space_cleaner = TestSpaceCleaner(self.data_reader.plugin_data_dirs)

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

        async def _memory_write_guard():
            guard = await _api_guard()
            if guard:
                return guard
            if not bool(self.settings_provider().get("memory_edit_enabled", False)):
                return jsonify({"ok": False, "error": "editing_disabled", "message": "记忆编辑功能未启用。"}), 403
            if not request.is_json:
                return jsonify({"ok": False, "error": "json_required", "message": "请求必须使用 JSON。"}), 415
            if request.headers.get("X-Requested-With") != "AlingDashboard":
                return jsonify({"ok": False, "error": "csrf_guard", "message": "请求来源校验失败。"}), 403
            return None

        async def _memory_payload() -> dict[str, Any]:
            payload = await request.get_json(silent=True) or {}
            return payload if isinstance(payload, dict) else {}

        def _memory_error(exc: MemoryEditorError):
            self.logger.warning("aling_life_dashboard memory editor rejected %s", exc.code)
            return jsonify({"ok": False, "error": exc.code, "message": exc.message}), exc.status

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

        @app.get("/api/memories")
        async def api_memories():
            guard = await _api_guard()
            if guard:
                return guard
            try:
                return jsonify(memory_editor.snapshot())
            except MemoryEditorError as exc:
                return _memory_error(exc)

        @app.post("/api/memories")
        async def api_memory_add():
            guard = await _memory_write_guard()
            if guard:
                return guard
            try:
                return jsonify({"ok": True, "item": memory_editor.add(await _memory_payload())})
            except MemoryEditorError as exc:
                return _memory_error(exc)

        @app.patch("/api/memories/<memory_id>")
        async def api_memory_update(memory_id: str):
            guard = await _memory_write_guard()
            if guard:
                return guard
            try:
                return jsonify({"ok": True, "item": memory_editor.update(memory_id, await _memory_payload())})
            except MemoryEditorError as exc:
                return _memory_error(exc)

        @app.post("/api/memories/<memory_id>/archive")
        async def api_memory_archive(memory_id: str):
            guard = await _memory_write_guard()
            if guard:
                return guard
            try:
                return jsonify({"ok": True, "item": memory_editor.set_status(memory_id, await _memory_payload(), "deprecated")})
            except MemoryEditorError as exc:
                return _memory_error(exc)

        @app.post("/api/memories/<memory_id>/restore")
        async def api_memory_restore(memory_id: str):
            guard = await _memory_write_guard()
            if guard:
                return guard
            try:
                return jsonify({"ok": True, "item": memory_editor.set_status(memory_id, await _memory_payload(), "active")})
            except MemoryEditorError as exc:
                return _memory_error(exc)

        @app.get("/api/test-space/status")
        async def api_test_space_status():
            guard = await _api_guard()
            if guard:
                return guard
            return jsonify(test_space_cleaner.snapshot())

        @app.post("/api/memory-candidates/<candidate_id>/approve")
        async def api_memory_candidate_approve(candidate_id: str):
            guard = await _memory_write_guard()
            if guard:
                return guard
            try:
                return jsonify({"ok": True, "item": memory_editor.approve_candidate(candidate_id, await _memory_payload())})
            except MemoryEditorError as exc:
                return _memory_error(exc)

        @app.post("/api/memory-candidates/<candidate_id>/reject")
        async def api_memory_candidate_reject(candidate_id: str):
            guard = await _memory_write_guard()
            if guard:
                return guard
            try:
                return jsonify({"ok": True, "item": memory_editor.reject_candidate(candidate_id, await _memory_payload())})
            except MemoryEditorError as exc:
                return _memory_error(exc)

        @app.post("/api/memories/preview")
        async def api_memory_preview():
            guard = await _api_guard()
            if guard:
                return guard
            if not request.is_json or request.headers.get("X-Requested-With") != "AlingDashboard":
                return jsonify({"ok": False, "error": "invalid_request", "message": "请求格式不正确。"}), 400
            try:
                return jsonify(memory_editor.preview(await _memory_payload()))
            except MemoryEditorError as exc:
                return _memory_error(exc)

        @app.post("/api/test-space/reset-memory")
        async def api_test_space_reset_memory():
            guard = await _memory_write_guard()
            if guard:
                return guard
            payload = await _memory_payload()
            if payload.get("confirmation") != "RESET_TEST_MEMORY":
                return jsonify({"ok": False, "error": "confirmation_required", "message": "需要确认后才能清空测试记忆。"}), 400
            try:
                removed = memory_editor.clear_test_scopes()
                removed.update(test_space_cleaner.clear())
                self.data_reader.invalidate_cache()
                return jsonify({"ok": True, "removed": removed})
            except MemoryEditorError as exc:
                return _memory_error(exc)

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
      color-scheme: light;
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --line: #e4e7ec;
      --text: #20242c;
      --muted: #667085;
      --accent: #f25f5c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      padding: 24px;
      background: var(--bg);
      color: var(--text);
    }}
    form {{
      width: min(420px, 100%);
      display: grid;
      gap: 16px;
      padding: 30px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--panel);
      box-shadow: 0 16px 44px rgba(16, 24, 40, 0.1);
    }}
    h1 {{ margin: 0; font-size: 26px; line-height: 1.15; letter-spacing: 0; }}
    p {{ margin: -6px 0 4px; color: var(--muted); font-size: 14px; line-height: 1.6; }}
    input {{
      height: 46px;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 0 14px;
      background: #fff;
      color: var(--text);
      font-size: 15px;
      outline: none;
    }}
    input:focus {{
      border-color: #f48b88;
      box-shadow: 0 0 0 3px rgba(242, 95, 92, 0.11);
    }}
    button {{
      height: 46px;
      border: 1px solid var(--accent);
      border-radius: 12px;
      background: var(--accent);
      color: #fff;
      font-weight: 760;
      cursor: pointer;
    }}
    button:hover {{ background: #e8514e; }}
    .error {{
      border: 1px solid #f0c4c1;
      border-radius: 12px;
      padding: 10px 12px;
      background: #fff1f0;
      color: #a72f2f;
      font-size: 14px;
    }}
  </style>
</head>
<body>
  <form method="post" action="/login">
    <h1>Aling Life Dashboard</h1>
    <p>输入 Dashboard 密码后查看状态并管理已授权的记忆内容。</p>
    {error_html}
    <input name="password" type="password" autocomplete="current-password" placeholder="Dashboard password" required autofocus>
    <button type="submit">进入 Dashboard</button>
  </form>
</body>
</html>"""
