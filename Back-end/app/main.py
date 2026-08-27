# ============================================================
# PaySentinelIQ — FastAPI Application Factory
# Non-blocking startup — healthcheck responds in <1 second.
# ============================================================

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.responses import Response

# ── Startup trace (stdout — visible in Railway logs) ──
print("[psi] Importing app.shared modules...", flush=True)

from app.shared.exceptions import PSIDomainError
from app.shared.middleware import RateLimitMiddleware
from app.shared.settings import get_settings

settings = get_settings()
print(f"[psi] Settings loaded: env={settings.ENVIRONMENT}", flush=True)

# ── Sentry — non-blocking, fails gracefully ──
print("[psi] Initializing Sentry...", flush=True)
try:
    from app.shared.sentry import init_sentry
    init_sentry()
    print("[psi] Sentry initialized", flush=True)
except Exception as exc:
    print(f"[psi] Sentry init skipped (non-fatal): {exc}", flush=True)

# ── Database engine — lazy, no connection at import time ──
print("[psi] Creating database engine...", flush=True)
try:
    from app.shared.database import engine
    print("[psi] Database engine created (lazy)", flush=True)
except Exception as exc:
    print(f"[psi] Database engine creation failed (non-fatal): {exc}", flush=True)
    engine = None  # type: ignore[assignment]

# ── Redis client import — function refs only, no connection ──
print("[psi] Importing Redis client...", flush=True)
try:
    from app.shared.redis_client import close_redis, get_redis
    print("[psi] Redis client imported", flush=True)
except Exception as exc:
    print(f"[psi] Redis client import failed (non-fatal): {exc}", flush=True)

    async def get_redis():  # type: ignore[no-redef]
        raise RuntimeError("Redis unavailable")

    async def close_redis():  # type: ignore[no-redef]
        pass

logger = logging.getLogger(__name__)


async def _background_init_redis() -> None:
    """Initialize Redis connection pool in the background."""
    try:
        await get_redis()
        logger.info("Redis connection pool initialized")
    except Exception as exc:
        logger.warning("Redis initialization deferred (non-fatal): %s", exc)


async def _background_init_llm() -> None:
    """Initialize LLM provider in the background."""
    try:
        from app.ai_agents.llm_service import get_llm_service

        llm_service = await get_llm_service()
        info = llm_service.get_info()
        logger.info(
            "LLM service initialized: provider=%s model=%s healthy=%s",
            info["provider"], info["model"], info["healthy"],
        )
    except Exception as exc:
        logger.warning("LLM service initialization deferred (non-fatal): %s", exc)


async def _background_run_migrations() -> None:
    """Ensure ALL critical tables exist on Supabase (non-blocking, idempotent)."""
    try:
        from app.shared.database import get_engine
        engine = get_engine()
        if engine is None:
            return
        # Use SQLAlchemy create_all for all registered models
        async with engine.begin() as conn:
            from app.shared.orm_models import Base
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Supabase tables verified/created (all models)")
    except Exception as exc:
        logger.warning("Table creation skipped (non-fatal): %s", exc)


# ── WebSocket Redis listener handle (for shutdown) ──
_ws_redis_task: asyncio.Task[None] | None = None
_domain_event_redis_task: asyncio.Task[None] | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Yields immediately — healthcheck responds in <1 second."""
    print("[psi] Lifespan started — scheduling background inits...", flush=True)

    global _ws_redis_task, _domain_event_redis_task

    asyncio.create_task(_background_init_redis())
    asyncio.create_task(_background_init_llm())
    asyncio.create_task(_background_run_migrations())  # Ensure Supabase tables exist

    # ── Start WebSocket Redis listener (Celery → WS bridge) ──
    try:
        from app.websocket.router import start_ws_redis_listener
        _ws_redis_task = asyncio.create_task(start_ws_redis_listener())
        print("[psi] WebSocket Redis listener started", flush=True)
    except Exception as exc:
        print(f"[psi] WebSocket Redis listener init skipped (non-fatal): {exc}", flush=True)

    # ── Start Domain Event Redis listener ──
    try:
        from app.events.event_bus import start_redis_event_listener
        _domain_event_redis_task = asyncio.create_task(start_redis_event_listener())
        print("[psi] Domain event Redis listener started", flush=True)
    except Exception as exc:
        print(f"[psi] Domain event Redis listener init skipped (non-fatal): {exc}", flush=True)

    print("[psi] Lifespan yielding — app is ready for /health", flush=True)
    yield

    # ── Shutdown ──
    # Cancel WebSocket Redis listener
    if _ws_redis_task is not None:
        _ws_redis_task.cancel()
        try:
            await _ws_redis_task
        except asyncio.CancelledError:
            pass
        print("[psi] WebSocket Redis listener stopped", flush=True)

    # Cancel domain event Redis listener
    if _domain_event_redis_task is not None:
        _domain_event_redis_task.cancel()
        try:
            await _domain_event_redis_task
        except asyncio.CancelledError:
            pass
        print("[psi] Domain event Redis listener stopped", flush=True)

    try:
        await close_redis()
    except Exception as exc:
        logger.warning("Redis shutdown error (non-fatal): %s", exc)
    try:
        from app.messaging.infrastructure.factory import close_event_publisher
        await close_event_publisher()
    except Exception as exc:
        logger.warning("Event publisher shutdown error (non-fatal): %s", exc)
    try:
        if engine is not None:
            await engine.dispose()
    except Exception as exc:
        logger.warning("DB engine dispose error (non-fatal): %s", exc)


def create_app() -> FastAPI:
    """Factory — returns a FastAPI app ready for /health immediately."""
    print("[psi] create_app() starting...", flush=True)

    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="AI-Powered Payroll Verification & Fraud Risk Intelligence Platform",
        docs_url="/api/docs" if settings.ENVIRONMENT != "production" else None,
        redoc_url="/api/redoc" if settings.ENVIRONMENT != "production" else None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    print("[psi] FastAPI instance created", flush=True)

    # ── CORS ──
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Rate Limiting middleware ──
    # Enforces Redis-based sliding window limits per route group.
    # Must come after CORS so we see the real client IP (via X-Forwarded-For)
    # but before route handlers so we can reject early.
    app.add_middleware(RateLimitMiddleware)

    # ── Security Headers middleware ──
    @app.middleware("http")
    async def add_security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response

    # ── Prometheus Metrics ──
    try:
        instrumentator = Instrumentator(
            should_group_status_codes=True,
            should_ignore_untemplated=True,
            should_instrument_requests_inprogress=True,
        )
        instrumentator.instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    except Exception as exc:
        print(f"[psi] Prometheus init skipped (non-fatal): {exc}", flush=True)

    # ── Global Exception Handler ──
    @app.exception_handler(PSIDomainError)
    async def domain_error_handler(request: Request, exc: PSIDomainError) -> JSONResponse:
        status_map: dict[str, int] = {
            "AUTHENTICATION_ERROR": 401, "AUTHORIZATION_ERROR": 403,
            "NOT_FOUND": 404, "ALREADY_EXISTS": 409,
            "VALIDATION_ERROR": 422, "RATE_LIMIT_EXCEEDED": 429,
        }
        return JSONResponse(
            status_code=status_map.get(exc.code, 400),
            content={"error": exc.code, "message": exc.message, "details": exc.details},
        )

    # ── Catch-all exception handler — never return 503 from /health ──
    @app.exception_handler(Exception)
    async def catchall_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": str(exc)},
        )

    # ── Root ──
    @app.get("/", tags=["Health"])
    async def root() -> dict[str, Any]:
        return {"name": settings.APP_NAME, "version": settings.APP_VERSION, "environment": settings.ENVIRONMENT}

    @app.get("/robots.txt", tags=["Health"], include_in_schema=False)
    async def robots() -> str:
        return "User-agent: *\nDisallow: /"

    @app.get("/sitemap.xml", tags=["Health"], include_in_schema=False)
    async def sitemap() -> str:
        return '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>\n'

    # ══════════════════════════════════════════════════════
    # HEALTH CHECK (Railway uses this)
    # ══════════════════════════════════════════════════════
    @app.get("/health")
    async def health_check() -> dict[str, Any]:
        """Minimal health check — always returns 200."""
        return {
            "status": "healthy",
            "service": "PaySentinelIQ",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/health/full")
    async def health_check_full() -> dict[str, Any]:
        """Deep check (DB, Redis, LLM). NOT used by Railway."""
        checks: dict[str, Any] = {
            "status": "healthy", "service": "PaySentinelIQ",
            "timestamp": datetime.now(timezone.utc).isoformat(), "dependencies": {},
        }
        # DB
        try:
            from sqlalchemy import text
            if engine is not None:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                checks["dependencies"]["database"] = {"status": "healthy"}
            else:
                checks["dependencies"]["database"] = {"status": "unavailable"}
        except Exception as exc:
            checks["status"] = "degraded"
            checks["dependencies"]["database"] = {"status": "unhealthy", "error": str(exc)}
        # Redis
        try:
            client = await get_redis()
            await client.ping()
            checks["dependencies"]["redis"] = {"status": "healthy"}
        except Exception as exc:
            checks["status"] = "degraded"
            checks["dependencies"]["redis"] = {"status": "unhealthy", "error": str(exc)}
        # RabbitMQ
        try:
            from app.observability.health import _check_rabbitmq
            checks["dependencies"]["rabbitmq"] = await _check_rabbitmq()
        except Exception as exc:
            checks["status"] = "degraded"
            checks["dependencies"]["rabbitmq"] = {"status": "unhealthy", "error": str(exc)}
        # LLM
        try:
            from app.ai_agents.llm_service import get_llm_service
            llm_svc = await get_llm_service()
            info = llm_svc.get_info()
            checks["dependencies"]["llm"] = {
                "status": "healthy" if info.get("healthy") else "unhealthy",
                "provider": info.get("provider", "unknown"),
                "model": info.get("model", "unknown"),
            }
        except Exception as exc:
            checks["dependencies"]["llm"] = {"status": "unhealthy", "error": str(exc)}
        return checks

    # ── Register Routers (lazy — each wrapped in try/except) ──
    print("[psi] Registering routers...", flush=True)
    _register_routers(app)
    print("[psi] All routers registered", flush=True)
    print("[psi] create_app() complete — returning app", flush=True)

    return app


def _register_routers(app: FastAPI) -> None:
    """Register all module routers. Each import is wrapped so one failure doesn't crash startup."""

    _safe_include(app, "app.observability.health", "", "Health")  # Fase 4
    _safe_include(app, "app.auth.presentation.router", "/api/auth", "Auth")
    _safe_include(app, "app.payroll.presentation.router", "/api/payrolls", "Payroll")
    _safe_include(app, "app.employees.presentation.router", "/api/employees", "Employees")
    _safe_include(app, "app.verification.presentation.router", "/api/verifications", "Verification")
    _safe_include(app, "app.fraud_detection.presentation.router", "/api/fraud-alerts", "Fraud Detection")
    _safe_include(app, "app.api.documents.router", "/api/documents", "Documents")  # Fase 3A — canonical
    _safe_include(app, "app.compliance.presentation.router", "/api/compliance", "Compliance")
    _safe_include(app, "app.audit.infrastructure.router", "/api/audit-logs", "Audit Logs")
    _safe_include(app, "app.notifications.infrastructure.router", "/api/notifications", "Notifications")
    _safe_include(app, "app.ai_assistant.presentation.router", "/api/ai-assistant", "AI Assistant")
    _safe_include(app, "app.settings_module.presentation.router", "/api/settings", "Settings")
    _safe_include(app, "app.analytics.application.router", "/api", "Analytics")
    _safe_include(app, "app.analytics.application.analysis_history_router", "/api", "Analysis History")
    _safe_include(app, "app.account.presentation.router", "/api", "Account")
    _safe_include(app, "app.breach_notification.infrastructure.router", "/api", "Breach Notifications")
    _safe_include(app, "app.websocket.router", "/ws", "WebSocket")

    if settings.ENVIRONMENT != "production":
        _safe_include(app, "app.shared.debug_router", "/api", "Debug")


def _safe_include(app: FastAPI, module_path: str, prefix: str, tag: str) -> None:
    """Import a router module and include it. Failure is logged, not fatal."""
    try:
        import importlib
        mod = importlib.import_module(module_path)
        router = getattr(mod, "router", None)
        if router is None:
            print(f"[psi] WARNING: No 'router' in {module_path}", flush=True)
            return
        app.include_router(router, prefix=prefix, tags=[tag])
        print(f"[psi]   Router loaded: {prefix} ({tag})", flush=True)
    except Exception as exc:
        print(f"[psi]   Router SKIPPED: {prefix} ({tag}) — {exc}", flush=True)
