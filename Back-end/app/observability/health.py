# ============================================================
# PaySentinelIQ — Health Checks (Fase 4)
# ============================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter

router = APIRouter(tags=["Health"])


@dataclass
class HealthStatus:
    healthy: bool = True
    checks: dict[str, Any] = field(default_factory=dict)


async def _check_database() -> dict:
    try:
        from app.shared.database import get_engine
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy", fromlist=["text"]).text("SELECT 1"))
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


async def _check_redis() -> dict:
    try:
        from app.shared.redis_client import get_redis
        redis = get_redis()
        await redis.ping()
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


async def _check_rabbitmq() -> dict:
    """Probe RabbitMQ with a real connection — NOT just 'process alive'."""
    try:
        from app.shared.settings import get_settings

        settings = get_settings()
        if not settings.RABBITMQ_ENABLED:
            return {"status": "disabled"}

        from app.messaging.infrastructure.rabbitmq_connection import (
            RabbitMQConnectionManager,
        )

        manager = RabbitMQConnectionManager()
        healthy = await manager.health_check()
        await manager.close()
        return {"status": "healthy" if healthy else "unhealthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


async def _check_llm() -> dict:
    try:
        from app.shared.settings import get_settings
        if not get_settings().ENABLE_AI_AGENTS:
            return {"status": "disabled"}
        from app.providers.factory import get_llm_provider
        provider = get_llm_provider()
        return {"status": "healthy" if provider.health_check() else "unhealthy",
                "provider": provider.get_info().provider_name}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


@router.get("/health")
async def health_check() -> dict:
    return {"status": "ok", "version": "1.0.0", "service": "PaySentinelIQ"}


@router.get("/ready")
async def readiness_check() -> dict:
    checks = {}
    db = await _check_database()
    checks["database"] = db
    redis = await _check_redis()
    checks["redis"] = redis
    rabbitmq = await _check_rabbitmq()
    checks["rabbitmq"] = rabbitmq
    healthy = db.get("status") == "healthy"
    return {"status": "ready" if healthy else "not_ready", "checks": checks}


@router.get("/live")
async def liveness_check() -> dict:
    checks = {}
    llm = await _check_llm()
    checks["llm"] = llm
    return {"status": "alive", "checks": checks}
