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

from openclear.api.scim import router as scim_router
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
from openclear.auth.sod import SodViolation
from openclear.web import rate_limit
from openclear.web.router import web_router
from openclear.web.security_headers import apply_security_headers, new_csp_nonce

_STATIC_DIR = Path(__file__).parent / "static"
_DOCS_SCREENSHOTS_DIR = Path(__file__).parent.parent.parent / "docs" / "screenshots"
logger = logging.getLogger(__name__)
access_logger = logging.getLogger("openclear.access")


_API_PREFIXES = ("/api/", "/scim/")
_STATIC_ERRORS_DIR = Path(__file__).parent / "static" / "errors"

# What a person sees for each error on a web page (templates/error.html). The API gets JSON.
_WEB_ERRORS = {
    400: ("That request couldn't be used", "Something in it wasn't valid. Go back, check what you entered, and try again."),
    401: ("Please sign in", "Sign in to see this page."),
    403: ("You don't have access to this", "Your account can't open this page. If you need it, ask an administrator."),
    404: ("Page not found", "There's no page at this address. It may have moved, or the link may be mistyped."),
    405: ("That link only works from a button", "This address is for a form or button, not for opening directly. Go back and use the "
                                                "button on the page."),
    413: ("That file is too large", "Files can be up to {limit}. Split it into smaller files, or compress it, and try again."),
    422: ("Part of that address or form wasn't valid", "Go back and try again. If you followed a link, it may be incomplete."),
    429: ("Too many requests", "You've made a lot of requests in a short time. Wait a minute, then try again."),
}


def _wants_json(request: Request) -> bool:
    return request.url.path.startswith(_API_PREFIXES)


def _web_error(request: Request, code: int, *, title: str | None = None, message: str | None = None,
               headers: dict | None = None, show_search: bool = False):
    """The branded error page (templates/error.html) for a web path."""
    default_title, default_message = _WEB_ERRORS.get(code, ("Something went wrong", "Go back and try again."))
    response = render_template(
        request, "error.html", status_code=code, status_code_display=code, title=title or default_title,
        message=message or default_message, show_back_link=True, show_search=show_search,
    )
    for key, value in (headers or {}).items():
        response.headers[key] = value
    return response


def _unavailable(request: Request, retry_after: int = 30):
    """503: the database can't be reached (or the app is starting or migrating). The page is
    static/errors/503.html, which needs nothing from the database to show."""
    headers = {"Retry-After": str(retry_after), "Cache-Control": "no-store"}
    if _wants_json(request):
        return JSONResponse({"detail": "Temporarily unavailable; try again shortly",
                             "request_id": getattr(request.state, "request_id", None)},
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, headers=headers)
    from fastapi.responses import HTMLResponse

    return HTMLResponse((_STATIC_ERRORS_DIR / "503.html").read_text(), status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        headers=headers)


def _static_cache_control(request: Request) -> str:
    """A versioned asset (`?v=` matching the file, web/templates.py::asset_version) never
    changes, so browsers and edges may keep it for a year; anything else, five minutes."""
    from openclear.web.templates import _asset_version

    version = request.query_params.get("v")
    relative = request.url.path.removeprefix("/static/")
    if version and version.isdigit() and int(version) == _asset_version(relative) != 0:
        return "public, max-age=31536000, immutable"
    return "public, max-age=300"


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
        or settings.enable_decision_outbox_scheduler
        or settings.enable_control_test_scheduler
        or settings.enable_security_event_delivery_scheduler
        or (settings.demo_tenant_enabled and settings.demo_tenant_reset_interval_minutes > 0)
    ):
        from openclear.workers.scheduler import start_scheduler, stop_scheduler

        scheduler = start_scheduler()

    if settings.reencrypt_secrets_at_startup:
        # Stored secrets still under Fernet or a rotated-out key move to AES-256-GCM under the
        # current key (auth/crypto.py). Best-effort, like the demo seeding below.
        from openclear.auth.crypto import reencrypt_all
        from openclear.db.session import get_session_factory

        session = get_session_factory()()
        try:
            if changed := reencrypt_all(session):
                logger.info("Re-encrypted %d stored secrets under the current key", changed)
                from openclear.services import security_events

                security_events.emit(session, "secrets.reencrypted", tenant_id=None, actor_type="system", details={"count": changed})
            session.commit()
        except Exception:  # noqa: BLE001 -- never stops the app starting
            session.rollback()
            logger.exception("Couldn't re-encrypt stored secrets at startup")
        finally:
            session.close()

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
    from openclear import logging_config

    logging_config.configure()

    app = FastAPI(title="OpenClear", version=_version(), lifespan=_lifespan)

    # Built-in and allowed plugins register their networks, providers and formats
    # (plugins/__init__.py). A new payment network is a plugin package, not an edit here.
    from openclear import plugins

    plugins.load()

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
            limit = get_settings().max_request_body_bytes
            if declared_size is not None and declared_size > limit:
                if request.url.path.startswith(_API_PREFIXES):
                    return JSONResponse({"detail": f"Request body too large (at most {limit} bytes)"},
                                        status_code=status.HTTP_413_CONTENT_TOO_LARGE)
                size = f"{limit // (1024 * 1024)} MB" if limit >= 1024 * 1024 else f"{limit:,} bytes"
                return _web_error(request, 413, message=_WEB_ERRORS[413][1].format(limit=size))
        return await call_next(request)

    @app.middleware("http")
    async def _rate_limit_global(request: Request, call_next):
        # A blanket safety net across every route (web/rate_limit.py). A signed-in person or
        # API client is limited as themselves, so a bank behind one NAT or edge address, or
        # one core system, isn't throttled as a single caller; anyone else by IP address
        # (web/request_context.py). Individual routes (e.g. /ui/markdown-preview, the token
        # endpoint) can layer a stricter limit of their own on top via
        # web.rate_limit.rate_limit().
        settings = get_settings()
        principal = rate_limit.principal_key(request)
        if principal:
            key, limit = principal, settings.rate_limit_per_principal_per_minute
        else:
            key, limit = get_client_ip(request) or "unknown", settings.rate_limit_per_minute
        if not rate_limit.allow(key, "global", limit=limit, window_seconds=60.0):
            from openclear.services import security_events

            security_events.emit_detached("rate_limit.exceeded", sampled_key=key, outcome="failure", reason="global",
                                          details={"limit_per_minute": limit, "keyed_by": "principal" if principal else "ip"})
            if request.url.path.startswith(_API_PREFIXES):
                return JSONResponse({"detail": "Too many requests; try again in a minute"},
                                    status_code=status.HTTP_429_TOO_MANY_REQUESTS, headers={"Retry-After": "60"})
            if request.url.path.startswith("/static/") or request.url.path == "/health":
                return PlainTextResponse("Too many requests -- please slow down.", status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                         headers={"Retry-After": "60"})
            return _web_error(request, 429, headers={"Retry-After": "60"})
        return await call_next(request)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        # Sets request.state.csp_nonce before either middleware above (or the route handler)
        # ever runs, so a template rendered deep inside can always find it, and it gets
        # to add headers to whatever response comes back out -- a normal page, a 429 from
        # the rate limiter above, a 413 from the body-size guard, or an exception-handler
        # response -- not just the happy path.
        request.state.csp_nonce = new_csp_nonce()
        response = await call_next(request)
        apply_security_headers(request, response)
        return response

    @app.middleware("http")
    async def _request_context(request: Request, call_next):
        # Registered last, so outermost: every request's context (client IP, request id;
        # who, once authenticated) is worked out once, here, for everything inside to read
        # (web/request_context.py). Then: the edge secret, if one is set (WAF_PLAN.md W2);
        # the request id on the response; cache headers for static files; the access log.
        import time

        from openclear.web import request_context

        context = request_context.build(request)
        request.state.request_id = context.request_id
        token = request_context.set_current(context)
        started = time.perf_counter()
        try:
            if not request_context.edge_secret_ok(request):
                from openclear.services import security_events

                security_events.emit_detached("edge.secret.refused", sampled_key=f"edge|{context.peer_ip}", outcome="failure",
                                              reason="missing_or_wrong_secret", details={"peer_ip": context.peer_ip})
                response = PlainTextResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
            else:
                response = await call_next(request)
            response.headers["X-Request-ID"] = context.request_id
            if request.url.path.startswith("/static/") and "cache-control" not in response.headers:
                response.headers["Cache-Control"] = _static_cache_control(request)
            access_logger.info(
                "%s %s %s", request.method, request.url.path, response.status_code,
                extra={"http": {"method": request.method, "path": request.url.path, "status": response.status_code,
                                "duration_ms": round((time.perf_counter() - started) * 1000, 1)}},
            )
            return response
        finally:
            request_context.reset(token)

    app.include_router(api_router)
    app.include_router(scim_router)
    # The web pages aren't API: keep them out of the published OpenAPI description.
    app.include_router(web_router, include_in_schema=False)
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

    from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
    from fastapi.exceptions import RequestValidationError
    from sqlalchemy.exc import InterfaceError, OperationalError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from openclear.web.security import CsrfFailed

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_error(request: Request, exc: StarletteHTTPException):
        # Every HTTP error raised for a web page (an unknown address, a method a page doesn't
        # take, a refusal) gets the branded page; the API and static files keep their usual
        # answers.
        if _wants_json(request) or request.url.path.startswith("/static/"):
            return await http_exception_handler(request, exc)
        if isinstance(exc, CsrfFailed):
            return _web_error(
                request, 403, title="This form expired",
                message="It was open long enough that its security check ran out, or you signed in again in another tab. "
                        "Nothing was saved. Reload the page, then enter it again.",
            )
        return _web_error(request, exc.status_code, headers=getattr(exc, "headers", None) or None,
                          show_search=exc.status_code == 404)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(request: Request, exc: RequestValidationError):
        if _wants_json(request):
            return await request_validation_exception_handler(request, exc)
        return _web_error(request, 422)

    @app.exception_handler(OperationalError)
    @app.exception_handler(InterfaceError)
    def _handle_database_unavailable(request: Request, exc: Exception):
        # The database can't be reached, or is locked or being migrated: temporary, so 503
        # with Retry-After rather than a 500. get_db's cleanup rolls back.
        logger.error("Database unavailable on %s %s: %s", request.method, request.url.path, exc)
        return _unavailable(request)

    @app.exception_handler(WebAuthRequired)
    def _handle_web_auth_required(request: Request, exc: WebAuthRequired) -> RedirectResponse:
        from openclear.web.security import safe_next_path

        from openclear.web.security import was_embedded

        next_path = safe_next_path(exc.next_path)
        if exc.try_resume:
            return RedirectResponse(f"/ui/auth/resume?next={quote(next_path)}", status_code=status.HTTP_303_SEE_OTHER)
        if was_embedded(request):  # a frame can't show the login page; tell its parent instead
            return RedirectResponse("/ui/embed/ended", status_code=status.HTTP_303_SEE_OTHER)
        url = "/ui/login" if next_path == "/ui/" else f"/ui/login?next={quote(next_path)}"
        return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)

    @app.exception_handler(WebPasswordChangeRequired)
    def _handle_web_password_change_required(request: Request, exc: WebPasswordChangeRequired) -> RedirectResponse:
        return RedirectResponse(f"{PASSWORD_CHANGE_PATH}?required=1", status_code=status.HTTP_303_SEE_OTHER)

    @app.exception_handler(SodViolation)
    def _handle_sod_violation(request: Request, exc: SodViolation):
        # Segregation of duties, enforce mode (auth/sod.py): already logged by the time this runs.
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": exc.body()}, status_code=status.HTTP_403_FORBIDDEN)
        return render_template(
            request, "error.html", status_code=status.HTTP_403_FORBIDDEN, status_code_display=403,
            message=f"{exc.violation.message}. Segregation of duties is enforced for this organization; someone else must do this.",
            title="Not allowed: segregation of duties", show_back_link=True,
        )

    from openclear.web.platform_session import PlatformAuthRequired, PlatformForbidden

    @app.exception_handler(PlatformAuthRequired)
    def _handle_platform_auth_required(request: Request, exc: PlatformAuthRequired) -> RedirectResponse:
        return RedirectResponse("/ui/platform/login", status_code=status.HTTP_303_SEE_OTHER)

    @app.exception_handler(PlatformForbidden)
    def _handle_platform_forbidden(request: Request, exc: PlatformForbidden):
        return render_template(request, "error.html", status_code=status.HTTP_403_FORBIDDEN, status_code_display=403,
                               message="That page is for platform operators.", show_back_link=True)

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
            response = JSONResponse({"detail": "Internal server error",
                                     "request_id": getattr(request.state, "request_id", None)}, status_code=500)
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
        if getattr(request.state, "request_id", None):
            response.headers["X-Request-ID"] = request.state.request_id
        return response

    from fastapi import Depends
    from sqlalchemy.orm import Session

    from openclear.db.session import get_db

    @app.get("/", include_in_schema=False)
    def front_door():
        """The bare address goes somewhere useful: on the developer sandbox, its sign-up page;
        anywhere else, the app (which asks to sign in first)."""
        return RedirectResponse("/ui/sandbox" if get_settings().sandbox_signup_enabled else "/ui/", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/health")
    def health(db: Session = Depends(get_db)):
        """For load balancers and uptime checks: "ok" only when the database answers, so a
        load balancer stops sending traffic to an instance that can't reach it."""
        from sqlalchemy import text

        try:
            db.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 -- any failure means not ready
            logger.exception("Health check: the database didn't answer")
            return JSONResponse({"status": "unavailable", "database": "unreachable"}, status_code=503,
                                headers={"Retry-After": "30", "Cache-Control": "no-store"})
        return {"status": "ok"}

    return app


app = create_app()
