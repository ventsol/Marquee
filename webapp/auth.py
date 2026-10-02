"""Single shared-secret auth for the admin dashboard.

This is a local admin tool, not a public site. One password in .env, stored in
a signed cookie session. If WEBAPP_PASSWORD is unset the app refuses to start
rather than silently exposing config-mutating routes.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

import config

log = logging.getLogger("trailer.web.auth")

SESSION_KEY = "trailer_admin"
PUBLIC_PATHS = {
    "/login",
    "/healthz",
    "/favicon.ico",
}
PUBLIC_PREFIXES = ("/static/",)


def password_configured() -> bool:
    return bool(config.WEBAPP_PASSWORD)


def verify_password(candidate: str) -> bool:
    """Constant-time comparison against the configured password."""
    if not password_configured():
        return False
    return hmac.compare_digest(candidate, config.WEBAPP_PASSWORD)


def is_authenticated(request: Request) -> bool:
    if not password_configured():
        return False
    return bool(request.session.get(SESSION_KEY))


def is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """Redirect unauthenticated browser requests to /login.

    API routes (path starts with /api/) get a 401 JSON response instead of a
    redirect, so fetch/HTMX callers aren't handed an HTML login page.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if not password_configured() or is_public(path):
            return await call_next(request)

        # Guard rather than assert: in Starlette, add_middleware prepends, so
        # this runs *outside* SessionMiddleware unless the app registers the
        # session layer after this one. Fail closed on a bare request.
        if "session" not in request.scope:
            if path.startswith("/api/"):
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    {"error": "unauthorized", "detail": "Session middleware missing."},
                    status_code=401,
                )
            return RedirectResponse(url="/login", status_code=302)

        if is_authenticated(request):
            return await call_next(request)

        if path.startswith("/api/"):
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {"error": "unauthorized", "detail": "Log in to use the API."},
                status_code=401,
            )

        return RedirectResponse(url=f"/login?next={path}", status_code=302)


def login_session(request: Request) -> None:
    request.session[SESSION_KEY] = True


def logout_session(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)


def safe_next(target: str | None) -> str:
    """Only allow same-site relative redirects (no open redirect)."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return "/"
    return target
