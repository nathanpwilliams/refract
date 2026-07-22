"""Auth: verify Supabase JWTs via JWKS, then load the caller's profile."""
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient
from fastapi import Depends, Header, HTTPException

from app.config import get_settings
from app import db


@dataclass
class Caller:
    user_id: str
    tenant_id: str
    role: str


_jwks_client: PyJWKClient | None = None


def _jwks() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        base = get_settings().supabase_url.rstrip("/")
        _jwks_client = PyJWKClient(f"{base}/auth/v1/.well-known/jwks.json")
    return _jwks_client


async def get_caller(authorization: str = Header(...)) -> Caller:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.removeprefix("Bearer ")
    try:
        signing_key = _jwks().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"Invalid token: {exc}")

    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "select id, tenant_id, role from profiles where id = $1", claims["sub"]
        )
    if row is None:
        raise HTTPException(403, "No profile for user")
    return Caller(user_id=str(row["id"]), tenant_id=str(row["tenant_id"]), role=row["role"])


async def require_author(caller: Caller = Depends(get_caller)) -> Caller:
    if caller.role not in ("owner", "author"):
        raise HTTPException(403, "Author role required")
    return caller
