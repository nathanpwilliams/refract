"""Auth: verify the Supabase JWT the frontend already holds, then load the
caller's profile (tenant_id + role). Authors/owners only for pipeline routes.
"""
from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException

from app.config import get_settings
from app import db


@dataclass
class Caller:
    user_id: str
    tenant_id: str
    role: str


async def get_caller(authorization: str = Header(...)) -> Caller:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.removeprefix("Bearer ")
    try:
        claims = jwt.decode(
            token,
            get_settings().supabase_jwt_secret,
            algorithms=["HS256"],
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
