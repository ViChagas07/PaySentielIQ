# ============================================================
# PaySentinelIQ — Auth Router (FastAPI)
# Login, register, MFA, token refresh, logout, Google OIDC
# LGPD-compliant: consent required for all authentication methods
# ============================================================

import logging
import uuid
from datetime import datetime, timezone as dt_timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_token_payload
from app.auth.repository import UserRepository
from app.auth.services import AuthService
from app.shared.database import get_db
from app.shared.exceptions import AuthenticationError, InvalidCredentialsError, TokenExpiredError
from app.shared.orm_models import ConsentRecordModel, TenantModel
from app.shared.settings import get_settings

settings = get_settings()
router = APIRouter()
logger = logging.getLogger(__name__)


# ── Request/Response Schemas ──


class LoginRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    email: EmailStr
    password: str = Field(min_length=8)
    terms_version: str | None = Field(default=None, min_length=1, max_length=20)
    privacy_version: str | None = Field(default=None, min_length=1, max_length=20)
    consent_given: bool = Field(default=False)


class MFAVerifyRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    session_token: str
    code: str = Field(pattern=r"^\d{6}$")


class TokenRefreshRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    refresh_token: str


class TokenResponse(BaseModel):
    model_config = ConfigDict(strict=True)
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    requires_mfa: bool = False
    mfa_session_token: str | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str
    email: str
    full_name: str
    role: str
    tenant_id: str
    mfa_enabled: bool
    avatar_url: str | None = None


class GoogleLoginRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    credential: str | None = Field(default=None, min_length=1)
    access_token: str | None = Field(default=None, min_length=1)
    terms_version: str | None = Field(default=None, min_length=1, max_length=20)
    privacy_version: str | None = Field(default=None, min_length=1, max_length=20)
    consent_given: bool = Field(default=False)


# ── Endpoints ──


@router.post("/google")
async def google_login(
    body: GoogleLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Google OIDC (Sign In With Google) login.

    Accepts either:
      - ``credential`` — a Google ID token JWT (legacy One Tap flow), OR
      - ``access_token`` — a Google OAuth 2.0 access token (GIS popup flow).

    Verifies the token, finds-or-creates the user, records LGPD consent,
    and returns a PaySentinelIQ JWT access token.

    Requires explicit consent to Terms of Service and Privacy Policy
    (LGPD Article 7 compliance).
    """
    # ── LGPD Consent Check ──
    if not body.consent_given:
        raise AuthenticationError(
            "Consent to Terms of Service and Privacy Policy is required (LGPD Article 7). "
            "Please accept the terms before signing in."
        )

    # ── 1. Verify the Google token ──────────────────────────────
    if body.credential:
        # ID token (JWT) path — used by the legacy One Tap flow
        try:
            google_user = id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
                body.credential,
                google_requests.Request(),
                settings.GOOGLE_CLIENT_ID,
            )
        except ValueError as exc:
            raise AuthenticationError(f"Invalid Google credential: {exc}") from exc
    elif body.access_token:
        # Access token path — used by the modern GIS popup flow
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {body.access_token}"},
                )
            if resp.status_code != 200:
                raise AuthenticationError("Invalid Google access token")
            google_user = resp.json()
        except httpx.RequestError as exc:
            raise AuthenticationError(f"Google userinfo request failed: {exc}") from exc
    else:
        raise AuthenticationError(
            "Either 'credential' or 'access_token' is required."
        )

    email: str = google_user["email"]
    name: str = google_user.get("name", email.split("@")[0])
    picture: str | None = google_user.get("picture")
    google_sub: str = google_user["sub"]

    # 2. Find or create user
    repo = UserRepository(db)
    user = await repo.get_by_email(email)

    if user is None:
        # Busca o tenant padrão pelo slug
        tenant_result = await db.execute(
            sa_select(TenantModel).where(TenantModel.slug == "default")
        )
        tenant = tenant_result.scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=500, detail="Default tenant not found")

        user = await repo.create(
            email=email,
            full_name=name,
            google_id=google_sub,
            tenant_id=tenant.id,
            avatar_url=picture,
        )
    else:
        # Update google_id, full_name, and avatar if user already exists
        if not user.google_id:
            user.google_id = google_sub
        if not user.avatar_url and picture:
            user.avatar_url = picture
        user.full_name = name
        await repo.update(user)

    # 3. Record consent (LGPD compliance)
    tv = body.terms_version if body.terms_version else settings.TERMS_VERSION
    pv = body.privacy_version if body.privacy_version else settings.PRIVACY_VERSION

    # Check if identical consent already exists (idempotent)
    existing_consent = await db.execute(
        sa_select(ConsentRecordModel).where(
            ConsentRecordModel.user_id == user.id,
            ConsentRecordModel.consent_type == "terms_of_service",
            ConsentRecordModel.terms_version == tv,
            ConsentRecordModel.privacy_version == pv,
        )
    )
    if not existing_consent.scalar_one_or_none():
        ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (
            request.client.host if request.client else None
        )
        ua = request.headers.get("User-Agent", "")[:500]
        consent_record = ConsentRecordModel(
            id=uuid.uuid4(),
            user_id=user.id,
            tenant_id=user.tenant_id,
            consent_type="terms_of_service",
            terms_version=tv,
            privacy_version=pv,
            accepted_at=datetime.now(dt_timezone.utc),
            ip_address=ip,
            user_agent=ua,
            method="oauth",
        )
        db.add(consent_record)

    # 4. Update last_login BEFORE issuing tokens
    user.last_login = datetime.now(dt_timezone.utc)
    await db.commit()
    await db.refresh(user)

    # 5. Issue PaySentinelIQ JWT + refresh token
    access_token = AuthService.create_access_token(
        user_id=str(user.id),
        tenant_id=str(user.tenant_id),
        role=user.role,
    )
    refresh_token_raw, _ = AuthService.create_refresh_token(str(user.id))

    return {
        "access_token": access_token,
        "refresh_token": refresh_token_raw,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": {
            "id": str(user.id),                    # UUID real do banco
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "tenant_id": str(user.tenant_id),
            "avatar_url": user.avatar_url,
            "mfa_enabled": user.mfa_enabled,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        },
    }


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, body: LoginRequest) -> TokenResponse:
    """
    Authenticate user. If MFA is enabled, returns a temporary session token
    instead of full access/refresh tokens.

    LGPD: Requires explicit consent to Terms of Service and Privacy Policy.
    """
    # ── LGPD Consent Check ──
    if not body.consent_given:
        raise HTTPException(
            status_code=403,
            detail="Consent to Terms of Service and Privacy Policy is required (LGPD Article 7).",
        )

    # In production, fetch user from repository
    # For demonstration, return mock tokens
    # Actual implementation would:
    # 1. Look up user by email
    # 2. Verify password
    # 3. Check if MFA is enabled
    # 4. Return tokens or MFA session

    # Mock: always return a valid token for demo purposes
    mock_user_id = str(uuid.uuid4())
    mock_tenant_id = str(uuid.uuid4())

    access_token = AuthService.create_access_token(
        user_id=mock_user_id,
        tenant_id=mock_tenant_id,
        role="fraud_analyst",
    )
    raw_refresh, hashed_refresh = AuthService.create_refresh_token(mock_user_id)

    await _publish_user_action(
        {"sub": mock_user_id, "tenant_id": mock_tenant_id},
        "login",
        ip_address=_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=900,  # 15 minutes
        requires_mfa=False,
    )


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/mfa/verify", response_model=TokenResponse)
async def verify_mfa(body: MFAVerifyRequest) -> TokenResponse:
    """
    Verify MFA code and issue full access/refresh tokens.
    """
    # In production:
    # 1. Validate session token from Redis
    # 2. Verify MFA code against user's secret
    # 3. Issue real tokens

    mock_user_id = str(uuid.uuid4())
    mock_tenant_id = str(uuid.uuid4())

    access_token = AuthService.create_access_token(
        user_id=mock_user_id,
        tenant_id=mock_tenant_id,
        role="fraud_analyst",
    )
    raw_refresh, _ = AuthService.create_refresh_token(mock_user_id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=900,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: TokenRefreshRequest) -> TokenResponse:
    """
    Exchange a valid refresh token for a new access/refresh token pair.
    Implements refresh token rotation.
    """
    try:
        payload = AuthService.decode_token(body.refresh_token)
        if payload.get("type") != "refresh":
            raise InvalidCredentialsError()

        user_id = payload["sub"]
        # In production: validate refresh token hash in DB, revoke old one

        # Issue new pair
        tenant_id = payload.get("tenant_id", str(uuid.uuid4()))
        role = payload.get("role", "viewer")

        new_access = AuthService.create_access_token(user_id, tenant_id, role)
        new_refresh_raw, _ = AuthService.create_refresh_token(user_id)

        return TokenResponse(
            access_token=new_access,
            refresh_token=new_refresh_raw,
            expires_in=900,
        )
    except TokenExpiredError:
        raise HTTPException(status_code=401, detail="Refresh token expired") from None
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token") from None


@router.get("/me", response_model=UserResponse)
async def get_current_user(payload: dict[str, Any] = Depends(get_token_payload)) -> UserResponse:
    """
    Return the currently authenticated user's profile.
    """
    return UserResponse(
        id=payload["sub"],
        email="analyst@paysentineliq.com",  # In production: fetch from DB
        full_name="Sarah Mitchell",
        role=payload["role"],
        tenant_id=payload["tenant_id"],
        mfa_enabled=True,
    )


@router.post("/logout")
async def logout(payload: dict[str, Any] = Depends(get_token_payload)) -> dict[str, Any]:
    """
    Logout: revoke all refresh tokens for this user.
    """
    # In production: revoke refresh tokens, clear sessions
    await _publish_user_action(payload, "logout")
    return {"message": "Logged out successfully"}


# ═══════════════════════════════════════════════════════════════
# EVENT PUBLISHING (user.action — feeds the activity history)
# ═══════════════════════════════════════════════════════════════


async def _publish_user_action(
    payload: dict[str, Any],
    action: str,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Publish a `user.action` event to the activity history (non-fatal)."""
    try:
        from app.messaging.domain.envelope import new_event
        from app.messaging.domain.event_types import EventType
        from app.messaging.infrastructure.factory import get_event_publisher

        event = new_event(
            EventType.USER_ACTION.value,
            payload={
                "action": action,
                "ip_address": ip_address,
                "user_agent": user_agent,
            },
            user_id=payload.get("sub"),
            tenant_id=payload.get("tenant_id"),
        )
        await get_event_publisher().publish(event)
    except Exception as exc:  # pragma: no cover - audit of auth must never fail
        logger.warning("user.action publish failed (non-fatal): %s", exc)
