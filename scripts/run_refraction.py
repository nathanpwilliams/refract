"""Run one pipeline v2 refraction end to end and print its warranty report.

Creates the target course_version + adaptation_jobs row the way the API's
POST /adaptation/jobs does, then drives it through app.services.pipeline
in-process — no uvicorn, no JWT, no worker poll. Use it for acceptance
runs and for re-running after a halt.

    source .venv/bin/activate
    python scripts/run_refraction.py --dry-run          # plan nothing, spend nothing
    python scripts/run_refraction.py --yes              # SPENDS API TOKENS

THIS SPENDS API TOKENS: one PLAN call, one REWRITE and one DISPOSITION
call per lesson with touched blocks, and TWO VERIFY calls per such lesson
(cross-model verification). --yes is required; --dry-run prints what would
run and the inputs it resolved, and exits.

Prints the touch rate, the deterministic check tally, merged findings, the
dispositions summary, and whether the version auto-published or halted.
"""
import argparse
import asyncio
import json
import sys

sys.path.insert(0, ".")

from app import db                                     # noqa: E402
from app.config import get_settings                    # noqa: E402
from app.services import pipeline                      # noqa: E402
from app.services import verifiers                     # noqa: E402

TENANT_SLUG = "demo"
COURSE_TITLE = "AI at Work"
PROFILE_NAME = "managers"


async def _resolve(conn, tenant_slug, course_title, profile_name):
    tenant_id = await conn.fetchval(
        "select id from tenants where slug = $1", tenant_slug)
    if tenant_id is None:
        raise SystemExit(f"No tenant with slug {tenant_slug!r}. Run scripts/seed.py.")

    course_id = await conn.fetchval(
        "select id from courses where tenant_id = $1 and title = $2",
        tenant_id, course_title)
    if course_id is None:
        raise SystemExit(f"No course titled {course_title!r} in tenant "
                         f"{tenant_slug!r}. Run scripts/ingest_canonical.py.")

    canon = await conn.fetchrow(
        """select id, status, learning_objectives from course_versions
           where tenant_id = $1 and course_id = $2 and is_canonical""",
        tenant_id, course_id)
    if canon is None:
        raise SystemExit("Course has no canonical version.")

    profile_id = await conn.fetchval(
        "select id from audience_profiles where tenant_id = $1 and name = $2",
        tenant_id, profile_name)
    if profile_id is None:
        raise SystemExit(f"No audience profile named {profile_name!r}.")

    block_count = await conn.fetchval(
        """select count(*) from content_blocks cb
           join lessons l on l.id = cb.lesson_id
           join modules m on m.id = l.module_id
           where m.tenant_id = $1 and m.version_id = $2""",
        tenant_id, canon["id"])
    lesson_count = await conn.fetchval(
        """select count(*) from lessons l join modules m on m.id = l.module_id
           where m.tenant_id = $1 and m.version_id = $2""",
        tenant_id, canon["id"])

    return {
        "tenant_id": tenant_id, "course_id": course_id,
        "canonical_version_id": canon["id"],
        "objectives": canon["learning_objectives"],
        "profile_id": profile_id,
        "blocks": block_count, "lessons": lesson_count,
    }


async def _next_label(conn, tenant_id, course_id, profile_name) -> str:
    used = {r["label"] for r in await conn.fetch(
        "select label from course_versions where tenant_id = $1 and course_id = $2",
        tenant_id, course_id)}
    n = 1
    while f"{profile_name} r{n}" in used:
        n += 1
    return f"{profile_name} r{n}"


def _report(evidence: dict, status: str, error: str | None) -> None:
    touch = evidence["touch_rate"]
    checks_summary = evidence["checks"]
    findings = evidence["findings"]
    dispositions = evidence["dispositions"]

    print("\n" + "=" * 68)
    print(f"OUTCOME: {evidence['outcome'].upper()}   (job status {status})")
    print("=" * 68)

    print(f"\nTouch rate: {touch['touched']}/{touch['total']} blocks "
          f"({touch['rate'] * 100:.1f}%)")
    for lesson in touch["by_lesson"]:
        print(f"    {lesson['touched']:>3}/{lesson['total']:<3} {lesson['title']}")
    if evidence["plan_warnings"]:
        print("\nPLAN warnings:")
        for w in evidence["plan_warnings"]:
            print(f"    ! {w}")

    print(f"\nDeterministic checks: {checks_summary['total']} run, "
          f"{checks_summary['failed']} failed")
    for check, tally in sorted(checks_summary["by_check"].items()):
        mark = "FAIL" if tally["fail"] else "ok  "
        print(f"    {mark} {check}: {tally['pass']} pass, {tally['fail']} fail")
    for f in checks_summary["failures"]:
        print(f"    ! {f['check']} on {f['block_id']}: {f['detail']}")

    print(f"\nVerifier findings: {findings['total']} "
          f"({findings['blockers']} blocker)")
    for source, tally in sorted(findings["by_source"].items()):
        print(f"    {source}: {tally['blocker']} blocker, {tally['advisory']} advisory")
    for f in findings["blocker_details"]:
        print(f"    ! {f['source']}/{f['issue']} on {f['block_id']}: {f['detail']}")

    print(f"\nDispositions: {dispositions['accepted']} accepted, "
          f"{dispositions['reverted']} reverted, {dispositions['halted']} halted "
          f"({dispositions['unchanged_by_rewrite']} returned unchanged by REWRITE)")
    for h in dispositions["halt_reasons"]:
        print(f"    ! halt on {h['block_id']}: {h['reason']}")

    print(f"\nCanonical digest: {evidence['canonical']['block_sha256'][:16]}… "
          f"over {evidence['canonical']['block_count']} blocks")
    print(f"Models: {json.dumps(evidence['models'])}")
    if error:
        print(f"\nHalt causes: {error}")
    print()


async def main(args) -> None:
    settings = get_settings()
    await db.init_pool()
    try:
        async with db.pool().acquire() as conn:
            ctx = await _resolve(conn, args.tenant_slug, args.course,
                                 args.profile)
            label = args.label or await _next_label(
                conn, ctx["tenant_id"], ctx["course_id"], args.profile)

        verifier_list = verifiers.build_verifiers()
        print(f"tenant            {args.tenant_slug}")
        print(f"course            {args.course!r}")
        print(f"canonical version {ctx['canonical_version_id']} "
              f"({ctx['lessons']} lessons, {ctx['blocks']} blocks)")
        print(f"audience profile  {args.profile!r}")
        print(f"new version label {label!r}")
        print(f"plan/rewrite/disposition model  {settings.adaptation_model}")
        for v in verifier_list:
            print(f"{v.spec.label:<32}{v.spec.provider}:{v.spec.model}")
        print(f"\nAPI calls: 1 PLAN + per lesson with touched blocks "
              f"(1 REWRITE + 2 VERIFY + 1 DISPOSITION), "
              f"up to {1 + ctx['lessons'] * 4} total.")

        if not args.yes:
            print("\nDRY RUN — nothing ran, no tokens spent. Re-run with --yes.")
            return

        async with db.pool().acquire() as conn:
            async with conn.transaction():
                target = await conn.fetchrow(
                    """insert into course_versions
                         (tenant_id, course_id, audience_profile_id, label,
                          status, learning_objectives)
                       values ($1, $2, $3, $4, 'generating', $5)
                       returning id""",
                    ctx["tenant_id"], ctx["course_id"], ctx["profile_id"],
                    label, ctx["objectives"])
                job = await conn.fetchrow(
                    """insert into adaptation_jobs
                         (tenant_id, source_version_id, target_version_id,
                          audience_profile_id, status)
                       values ($1, $2, $3, $4, 'queued')
                       returning *""",
                    ctx["tenant_id"], ctx["canonical_version_id"],
                    target["id"], ctx["profile_id"])

        print(f"\njob               {job['id']}")
        print(f"target version    {target['id']}")
        print("\nrunning pipeline v2 …")
        try:
            await pipeline.run_job(dict(job))
        except Exception as exc:
            await db.pool().execute(
                """update adaptation_jobs
                   set status = 'failed', error = $2, finished_at = now()
                   where id = $1""",
                job["id"], str(exc)[:2000])
            raise
        await db.pool().execute(
            "update adaptation_jobs set finished_at = now() where id = $1",
            job["id"])

        row = await db.pool().fetchrow(
            "select status, error, evidence from adaptation_jobs where id = $1",
            job["id"])
        _report(json.loads(row["evidence"]), row["status"], row["error"])
        print(f"evidence: GET /adaptation/jobs/{job['id']}/evidence")
    finally:
        await db.close_pool()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tenant-slug", default=TENANT_SLUG)
    p.add_argument("--course", default=COURSE_TITLE)
    p.add_argument("--profile", default=PROFILE_NAME)
    p.add_argument("--label", default=None,
                   help="version label (default: '<profile> rN', auto-incremented)")
    p.add_argument("--yes", action="store_true",
                   help="actually run — SPENDS API TOKENS")
    p.add_argument("--dry-run", action="store_true",
                   help="resolve inputs and print the plan only (default)")
    args = p.parse_args()
    if args.dry_run:
        args.yes = False
    asyncio.run(main(args))
