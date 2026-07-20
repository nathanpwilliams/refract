"""In-process background worker.

Polls adaptation_jobs for status='queued', claims one row at a time with
FOR UPDATE SKIP LOCKED (safe even if you later run multiple instances),
and drives it through the pipeline. Deliberately boring: no Celery, no
Redis — one table is the queue. Swap for a real queue if volume ever
justifies it.
"""
import asyncio
import logging

from app import db
from app.config import get_settings
from app.services.pipeline import run_job

log = logging.getLogger("worker")


async def _claim_next_job() -> dict | None:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """select * from adaptation_jobs
                   where status = 'queued'
                   order by created_at
                   for update skip locked
                   limit 1"""
            )
            if row is None:
                return None
            await conn.execute(
                "update adaptation_jobs set status = 'planning' where id = $1",
                row["id"],
            )
            return dict(row)


async def worker_loop(stop: asyncio.Event) -> None:
    poll = get_settings().worker_poll_seconds
    while not stop.is_set():
        job = None
        try:
            job = await _claim_next_job()
            if job is None:
                await asyncio.wait_for(stop.wait(), timeout=poll)
                continue
            log.info("running adaptation job %s", job["id"])
            await run_job(job)
            await db.pool().execute(
                "update adaptation_jobs set finished_at = now() where id = $1",
                job["id"],
            )
        except asyncio.TimeoutError:
            continue
        except Exception as exc:  # noqa: BLE001 — job failures must not kill the loop
            log.exception("adaptation job failed")
            if job is not None:
                await db.pool().execute(
                    """update adaptation_jobs
                       set status = 'failed', error = $2, finished_at = now()
                       where id = $1""",
                    job["id"], str(exc)[:2000],
                )
