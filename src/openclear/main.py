# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Chaffed

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.exc import DataError, IntegrityError
from fastapi.staticfiles import StaticFiles

from openclear.api.v1.router import api_router
from openclear.config import assert_production_safe, get_settings
from openclear.web.client_ip import get_client_ip
from openclear.web.deps import (
    PASSWORD_CHANGE_PATH,
    WebAuthRequired,
    WebForbidden,
    WebNotFound,
    WebPasswordChangeRequired,
    render_template,
)
from openclear.web import rate_limit
from openclear.web.router import web_router
from openclear.web.security_headers import apply_security_headers, new_csp_nonce

_STATIC_DIR = Path(__file__).parent / "static"
_DOCS_SCREENSHOTS_DIR = Path(__file__).parent.parent.parent / "docs" / "screenshots"
logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    scheduler = None
    settings = get_settings()
    if (
        settings.enable_ml_scheduler
        or settings.auto_import_enabled
        or settings.notifications_enabled
        or settings.enable_disposition_scheduler
        or settings.enable_housekeeping_scheduler
        or (settings.demo_tenant_enabled and settings.demo_tenant_reset_interval_minutes > 0)
    ):
        from openclear.workers.scheduler import start_scheduler, stop_scheduler

        scheduler = start_scheduler()

    if settings.demo_tenant_enabled:
        # Idempotent -- a safe no-op on every restart after the first. Best-effort: a
        # seeding failure (e.g. the DB isn't migrated yet) must never prevent the app
        # itself from starting.
        from openclear.db.session import get_session_factory
        from openclear.services.demo_tenant_service import DemoTenantNotConfigured, ensure_demo_tenant

        session = get_session_factory()()
        try:
            ensure_demo_tenant(session)
        except DemoTenantNotConfigured as exc:
            logger.warning("Demo tenant not provisioned at startup: %s", exc)
        except Exception:  # noqa: BLE001 -- seeding failure must never block app startup
            logger.exception("Failed to ensure the demo tenant exists at startup")
            session.rollback()
        finally:
            session.close()

    # Background jobs (workers/jobs.py): with worker_mode "in_process" (the default) this
    # process runs one worker thread; with "external", separate `openclear-worker`
    # processes do; "inline" is the test suite.
    worker_started = False
    if settings.worker_mode == "in_process":
        from openclear.db.session import get_engine
        from openclear.workers.jobs import start_in_process_worker

        start_in_process_worker(get_engine())
        worker_started = True

    yield
    if worker_started:
        from openclear.workers.jobs import stop_in_process_worker

        stop_in_process_worker()
    if scheduler is not None:
        stop_scheduler()


def _version() -> str:
    """The installed package's version (pyproject.toml), so the API docs never go stale."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("openclear")
    except PackageNotFoundError:  # running from a source tree that isn't installed
        return "0.0.0+unknown"


def create_app() -> FastAPI:
    # First thing, before anything else — a production deployment still pointing at
    # the checked-in dev/test signing keys or the default SSO encryption key must never
    # even finish starting (see SECURITY_REVIEW.md and config.py::assert_production_safe).
    assert_production_safe(get_settings())

    app = FastAPI(title="OpenClear", version=_version(), lifespan=_lifespan)

    # Importing each network package triggers its register_adapter() call at import
    # time (see networks/registry.py). Adding a new network later means adding one
    # import here plus its own package — nothing else in this file changes.
    import openclear.networks.check  # noqa: F401
    import openclear.networks.ach  # noqa: F401

    # Registered first, so it's the innermost: the body-size guard, rate limit and security
    # headers below all run before it does (Starlette wraps in reverse order).
    from openclear.api import idempotency

    idempotency.install(app)

    @app.middleware("http")
    async def _limit_request_body_size(request: Request, call_next):
        # Fast, uniform first line of defense against oversized uploads — reads
        # Content-Length off every incoming request before any route handler (or
        # per-parser code) ever sees the body. Doesn't catch a chunked-transfer-encoded
        # request omitting Content-Length; not adding a full streaming body-size
        # enforcer for that, same proportionality call as elsewhere in this pass — see
        # SECURITY_REVIEW.md.
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = None
            if declared_size is not None and declared_size > get_settings().max_request_body_bytes:
                return PlainTextResponse("Request body too large", status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        return await call_next(request)

    @app.middleware("http")
    async def _rate_limit_global(request: Request, call_next):
        # A blanket per-IP safety net across every route -- see web/rate_limit.py for the
        # in-memory sliding-window design and web/client_ip.py for how the IP itself is
        # resolved (direct connection by default; trusts X-Forwarded-For only if
        # OPENCLEAR_TRUSTED_PROXY_COUNT says a reverse proxy/WAF is actually in front of
        # this). Individual routes (e.g. /ui/markdown-preview) can layer a stricter limit
        # of their own on top via web.rate_limit.rate_limit(), tracked as a separate
        # bucket under the same client key.
        client_ip = get_client_ip(request) or "unknown"
        if not rate_limit.allow(client_ip, "global", limit=get_settings().rate_limit_per_minute, window_seconds=60.0):
            return PlainTextResponse("Too many requests -- please slow down.", status_code=status.HTTP_429_TOO_MANY_REQUESTS)
        return await call_next(request)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        # Registered last, deliberately: Starlette's middleware stack wraps in reverse
        # registration order, so the last one registered here is outermost -- it sets
        # request.state.csp_nonce before either middleware above (or the route handler)
        # ever runs, so a template rendered deep inside can always find it, and it gets
        # to add headers to whatever response comes back out -- a normal page, a 429 from
        # the rate limiter above, a 413 from the body-size guard, or an exception-handler
        # response -- not just the happy path.
        request.state.csp_nonce = new_csp_nonce()
        response = await call_next(request)
        apply_security_headers(request, response)
        return response

    app.include_router(api_router)
    app.include_router(web_router)
    # Registered before the broader "/static" mount below -- Starlette matches mounts by
    # prefix in registration order, so the more specific path must come first or every
    # request under it would be swallowed by "/static" (and 404, since that directory has
    # no "docs-screenshots" subfolder of its own).
    app.mount(
        "/static/docs-screenshots",
        StaticFiles(directory=str(_DOCS_SCREENSHOTS_DIR), check_dir=False),
        name="docs_screenshots",
    )
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.exception_handler(WebAuthRequired)
    def _handle_web_auth_required(request: Request, exc: WebAuthRequired) -> RedirectResponse:
        from openclear.web.security import safe_next_path

        next_path = safe_next_path(exc.next_path)
        if exc.try_resume:
            return RedirectResponse(f"/ui/auth/resume?next={quote(next_path)}", status_code=status.HTTP_303_SEE_OTHER)
        url = "/ui/login" if next_path == "/ui/" else f"/ui/login?next={quote(next_path)}"
        return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)

    @app.exception_handler(WebPasswordChangeRequired)
    def _handle_web_password_change_required(request: Request, exc: WebPasswordChangeRequired) -> RedirectResponse:
        return RedirectResponse(f"{PASSWORD_CHANGE_PATH}?required=1", status_code=status.HTTP_303_SEE_OTHER)

    @app.exception_handler(WebForbidden)
    def _handle_web_forbidden(request: Request, exc: WebForbidden):
        return render_template(
            request,
            "error.html",
            status_code=status.HTTP_403_FORBIDDEN,
            status_code_display=403,
            message=exc.message or "You don't have permission to do that.",
            title=exc.title,
            show_back_link=True,
        )

    @app.exception_handler(WebNotFound)
    def _handle_web_not_found(request: Request, exc: WebNotFound):
        return render_template(
            request,
            "error.html",
            status_code=status.HTTP_404_NOT_FOUND,
            status_code_display=404,
            message="That item couldn't be found.",
            show_back_link=True,
        )

    @app.exception_handler(IntegrityError)
    def _handle_integrity_error(request: Request, exc: IntegrityError):
        # Backstop for a database uniqueness conflict no route pre-checked for (e.g. a
        # duplicate customer number or security group name). Routes with a form should
        # still catch this themselves and re-render the form with an inline error (see
        # web/routers/accounts.py::create_account) — this just guarantees the worst case
        # is a clear 409, not a 500. get_db's cleanup rolls the failed transaction back.
        logger.info("Uniqueness conflict on %s %s: %s", request.method, request.url.path, exc.orig)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Conflicts with an existing record"}, status_code=status.HTTP_409_CONFLICT)
        return render_template(
            request,
            "error.html",
            status_code=status.HTTP_409_CONFLICT,
            status_code_display=409,
            message="That conflicts with a record that already exists (for example, a duplicate name or number). "
            "Go back, change it, and try again.",
            show_back_link=True,
        )

    @app.exception_handler(DataError)
    def _handle_data_error(request: Request, exc: DataError):
        # Backstop for a value the database rejects: too long for its column, or out of
        # range. Postgres and SQL Server enforce column sizes; SQLite doesn't, so this
        # never shows up in local development. A 400 rather than a 500: the input was
        # wrong, not the server. get_db's cleanup rolls the failed transaction back.
        logger.info("Rejected value on %s %s: %s", request.method, request.url.path, exc.orig)
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                {"detail": "A value is too long or not valid for its field"}, status_code=status.HTTP_400_BAD_REQUEST
            )
        return render_template(
            request,
            "error.html",
            status_code=status.HTTP_400_BAD_REQUEST,
            status_code_display=400,
            message="One of the values you entered is too long or isn't valid for its field. "
            "Go back, shorten or correct it, and try again.",
            show_back_link=True,
        )

    @app.exception_handler(Exception)
    def _handle_unexpected_error(request: Request, exc: Exception):
        # Last resort for anything no other handler claims. Starlette runs this from its
        # outermost ServerErrorMiddleware and re-raises the exception afterwards, so the
        # server still logs the full traceback — this only controls what the browser
        # sees: a branded page with a way back (or plain JSON under /api), never the
        # exception text. Being outermost, the security-headers middleware never saw this
        # response, so apply them here explicitly.
        if request.url.path.startswith("/api/"):
            response = JSONResponse({"detail": "Internal server error"}, status_code=500)
        else:
            response = render_template(
                request,
                "error.html",
                status_code=500,
                status_code_display=500,
                message="Something went wrong on our end. Nothing you entered was saved — please try again, "
                "and contact support if it keeps happening.",
                show_back_link=True,
            )
        apply_security_headers(request, response)
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
