from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from quart import Request, Response, redirect, request


SESSION_COOKIE_NAME = "aling_life_dashboard_session"
SESSION_TTL_HOURS = 12


@dataclass
class SessionRecord:
    token: str
    expires_at: datetime


class DashboardAuth:
    def __init__(self, settings_provider):
        self.settings_provider = settings_provider
        self._sessions: dict[str, SessionRecord] = {}

    def login(self, password: str) -> str | None:
        expected = str(self.settings_provider().get("dashboard_password", ""))
        if not expected:
            return None
        if not secrets.compare_digest(password, expected):
            return None
        token = secrets.token_urlsafe(32)
        self._sessions[token] = SessionRecord(
            token=token,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS),
        )
        return token

    def logout(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)

    def is_authorized_request(self, req: Request | None = None) -> bool:
        req = req or request
        token = req.cookies.get(SESSION_COOKIE_NAME, "")
        if not token:
            return False
        record = self._sessions.get(token)
        if record is None:
            return False
        if record.expires_at <= datetime.now(timezone.utc):
            self._sessions.pop(token, None)
            return False
        return True

    def current_token(self, req: Request | None = None) -> str:
        req = req or request
        return req.cookies.get(SESSION_COOKIE_NAME, "")


def set_login_cookie(response: Response, token: str) -> Response:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="Lax",
    )
    return response


def clear_login_cookie(response: Response) -> Response:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


def login_redirect() -> Response:
    return redirect("/login")
