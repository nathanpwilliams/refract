"""asyncpg pool.

Architecture note: this FastAPI service connects with the SERVICE ROLE and
therefore BYPASSES row-level security. RLS protects the frontend's direct
Supabase reads/writes; here in the backend, every query must be explicitly
tenant-scoped. The fetch/execute helpers below require a tenant_id argument
on purpose — it makes 'forgot the tenant filter' a signature error instead
of a data leak.
"""
import asyncpg
from app.config import get_settings

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=get_settings().database_url,
            min_size=1,
            max_size=10,
            statement_cache_size=0,   # required behind Supabase's pgbouncer pooler
        )


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized; call init_pool() on startup")
    return _pool


async def fetch_tenant(tenant_id: str, query: str, *args):
    """fetch rows; query MUST reference tenant_id = $1."""
    assert "tenant_id" in query, "query is missing a tenant_id predicate"
    async with pool().acquire() as conn:
        return await conn.fetch(query, tenant_id, *args)


async def fetchrow_tenant(tenant_id: str, query: str, *args):
    assert "tenant_id" in query, "query is missing a tenant_id predicate"
    async with pool().acquire() as conn:
        return await conn.fetchrow(query, tenant_id, *args)


async def execute_tenant(tenant_id: str, query: str, *args):
    assert "tenant_id" in query, "query is missing a tenant_id predicate"
    async with pool().acquire() as conn:
        return await conn.execute(query, tenant_id, *args)
