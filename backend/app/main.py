import asyncio
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import models  # noqa: F401
from app.api.routes import router
from app.auth import COOKIE_NAME, decode_access_token, ensure_default_admin, is_setup_complete
from app.config import get_settings
from app.db import Base, SessionLocal, engine, ensure_sqlite_runtime_schema
from app.logging_utils import get_logger, sanitize_log_context
from app.services.mail_ingest import poll_mailbox_and_enqueue
from app.services.nas import run_nas_scan
from app.services.qdrant import embed_texts_async, ensure_collection_exists

settings = get_settings()
logger = get_logger(__name__)
is_production_env = os.getenv("ENV", "").strip().lower() == "production"


async def _mail_poll_loop(stop_event: asyncio.Event) -> None:
    interval = max(30, int(settings.mail_poll_interval_sec))
    while not stop_event.is_set():
        db = SessionLocal()
        try:
            out = poll_mailbox_and_enqueue(db)
            if str(out.get("detail") or "") not in {"", "ok"}:
                logger.warning(
                    "mail_poll_warn",
                    extra=sanitize_log_context(
                        {
                            "status": "warn",
                            "detail": str(out.get("detail") or ""),
                            "polled_messages": int(out.get("polled_messages") or 0),
                        }
                    ),
                )
        except Exception as exc:
            logger.warning(
                "mail_poll_loop_failed",
                extra=sanitize_log_context(
                    {
                        "status": "warn",
                        "error_code": "mail_poll_loop_failed",
                        "detail": str(exc),
                    }
                ),
            )
        finally:
            db.close()

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


async def _nas_scan_loop(stop_event: asyncio.Event) -> None:
    interval = max(60, int(settings.nas_scan_interval_sec))
    while not stop_event.is_set():
        db = SessionLocal()
        try:
            run_nas_scan(db, paths=[settings.nas_default_source_dir], recursive=True)
        except Exception as exc:
            logger.warning(
                "nas_scan_loop_failed",
                extra=sanitize_log_context(
                    {
                        "status": "warn",
                        "error_code": "nas_scan_loop_failed",
                        "detail": str(exc),
                    }
                ),
            )
        finally:
            db.close()

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


@asynccontextmanager
async def lifespan(_app: FastAPI):
    stop_event = asyncio.Event()
    background_tasks: list[asyncio.Task] = []
    if settings.auto_create_schema and not is_production_env:
        Base.metadata.create_all(bind=engine)
        ensure_sqlite_runtime_schema()
    db = SessionLocal()
    try:
        ensure_default_admin(db)
    finally:
        db.close()
    if settings.qdrant_enable:
        try:
            ensure_collection_exists(force=True)
        except Exception as exc:
            logger.warning(
                "qdrant_init_failed",
                extra=sanitize_log_context(
                    {
                        "status": "warn",
                        "error_code": "qdrant_init_failed",
                        "detail": str(exc),
                    }
                ),
            )
        try:
            logger.info("embed_warmup_start")
            await embed_texts_async(["warmup"])
            logger.info(
                "embed_warmup_done",
                extra=sanitize_log_context({"status": "ok", "warmup_items": 1}),
            )
        except Exception as exc:
            logger.warning(
                "embed_warmup_failed",
                extra=sanitize_log_context(
                    {
                        "status": "warn",
                        "error_code": "embed_warmup_failed",
                        "detail": str(exc),
                    }
                ),
            )
    if settings.nas_auto_scan_enabled:
        background_tasks.append(asyncio.create_task(_nas_scan_loop(stop_event)))
    if settings.mail_poll_enabled:
        background_tasks.append(asyncio.create_task(_mail_poll_loop(stop_event)))
    yield
    stop_event.set()
    for task in background_tasks:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


_AUTH_EXEMPT_PREFIXES = (
    "/v1/auth/",
    "/api/v1/auth/",
    "/health",
    "/",
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Require a valid JWT cookie for all non-exempt routes."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Allow CORS preflight and exempt paths
        if request.method == "OPTIONS":
            return await call_next(request)
        if any(path == p or path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES):
            return await call_next(request)

        token = request.cookies.get(COOKIE_NAME)
        if not token or not decode_access_token(token):
            # If setup is not complete yet, let the request through so the
            # frontend can redirect to /setup.
            db = SessionLocal()
            try:
                setup_done = is_setup_complete(db)
            finally:
                db.close()
            if not setup_done:
                return await call_next(request)
            return JSONResponse(status_code=401, content={"detail": "Not authenticated."})

        return await call_next(request)


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

allow_all_origins = len(settings.allowed_origins) == 1 and settings.allowed_origins[0] == "*"
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=not allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AuthMiddleware)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": settings.app_name, "version": settings.version}


app.include_router(router)
