"""Adaptation pipeline orchestration.

run_job() drives one adaptation_jobs row through:
    planning -> rewriting -> verifying -> awaiting_review

It copies the canonical version tree (modules/lessons/blocks) into the target
version, rewrites adaptable blocks lesson-by-lesson, records original/generated
pairs in block_revisions, and attaches verifier findings. Nothing is published
here — publishing happens only through the review endpoints.
"""
import json
import uuid

from app import db
from app.config import get_settings
from app.services import llm
from app.services import prompts


async def run_job(job: dict) -> None:
    tenant_id = str(job["tenant_id"])
    job_id = str(job["id"])
    source_version = str(job["source_version_id"])
    target_version = str(job["target_version_id"])

    profile = await _load_audience_profile(tenant_id, str(job["audience_profile_id"]))
    objectives = await _load_objectives(tenant_id, source_version)

    # ---- Stage 1: PLAN -------------------------------------------------
    await _set_status(tenant_id, job_id, "planning")
    outline = await _course_outline(tenant_id, source_version)
    plan = await llm.complete_json(
        prompts.PLAN_SYSTEM,
        prompts.PLAN_USER_TEMPLATE.format(
            audience_profile_json=json.dumps(profile),
            course_outline_json=json.dumps(outline),
        ),
        temperature=get_settings().rewrite_temperature,
    )
    await db.execute_tenant(
        tenant_id,
        "update adaptation_jobs set plan = $2 where tenant_id = $1 and id = $3",
        json.dumps(plan), job_id,
    )
    lesson_plans = {p["lesson_id"]: p for p in plan.get("lessons", [])}

    # ---- Copy tree, then Stage 2 + 3 per lesson ------------------------
    await _set_status(tenant_id, job_id, "rewriting")
    lesson_map = await _copy_version_tree(tenant_id, source_version, target_version)

    for source_lesson_id, target_lesson_id in lesson_map.items():
        blocks = await _load_blocks(tenant_id, source_lesson_id)
        adaptable = [b for b in blocks if b["adaptable"]]
        if not adaptable:
            continue

        lesson_title = await _lesson_title(tenant_id, source_lesson_id)
        rewrite = await llm.complete_json(
            prompts.REWRITE_SYSTEM,
            prompts.REWRITE_USER_TEMPLATE.format(
                audience_profile_json=json.dumps(profile),
                lesson_plan_json=json.dumps(lesson_plans.get(source_lesson_id, {})),
                objectives_json=json.dumps(objectives),
                lesson_title=lesson_title,
                blocks_json=json.dumps([_block_payload(b) for b in adaptable]),
            ),
            temperature=get_settings().rewrite_temperature,
        )
        generated = {r["block_id"]: r for r in rewrite.get("blocks", [])}

        verify = await llm.complete_json(
            prompts.VERIFY_SYSTEM,
            prompts.VERIFY_USER_TEMPLATE.format(
                objectives_json=json.dumps(objectives),
                pairs_json=json.dumps([
                    {
                        "block_id": str(b["id"]),
                        "original": b["content"],
                        "adapted": generated.get(str(b["id"]), {}).get("content"),
                    }
                    for b in adaptable
                ]),
            ),
            temperature=get_settings().verify_temperature,
        )
        findings_by_block: dict[str, list] = {}
        for f in verify.get("findings", []):
            findings_by_block.setdefault(f["block_id"], []).append(f)

        for b in adaptable:
            bid = str(b["id"])
            gen = generated.get(bid)
            if gen is None:
                continue
            new_content = b["content"] if gen.get("unchanged") else gen.get("content")
            # write adapted content onto the target version's copied block
            await db.execute_tenant(
                tenant_id,
                """update content_blocks set content = $2
                   where tenant_id = $1 and lesson_id = $3 and source_block_id = $4""",
                json.dumps(new_content), target_lesson_id, bid,
            )
            await db.execute_tenant(
                tenant_id,
                """insert into block_revisions
                     (tenant_id, job_id, block_id, original, generated, verifier_notes)
                   values ($1, $2, $3, $4, $5, $6)""",
                job_id, bid,
                json.dumps(b["content"]),
                json.dumps(gen),
                json.dumps(findings_by_block.get(bid, [])),
            )

    await _set_status(tenant_id, job_id, "awaiting_review")
    await db.execute_tenant(
        tenant_id,
        """update course_versions set status = 'generated'
           where tenant_id = $1 and id = $2""",
        target_version,
    )


# ---------------------------------------------------------------- helpers

async def _set_status(tenant_id: str, job_id: str, status: str) -> None:
    await db.execute_tenant(
        tenant_id,
        "update adaptation_jobs set status = $2 where tenant_id = $1 and id = $3",
        status, job_id,
    )


async def _load_audience_profile(tenant_id: str, profile_id: str) -> dict:
    row = await db.fetchrow_tenant(
        tenant_id,
        "select name, attributes from audience_profiles where tenant_id = $1 and id = $2",
        profile_id,
    )
    return {"name": row["name"], **json.loads(row["attributes"])}


async def _load_objectives(tenant_id: str, version_id: str) -> list:
    row = await db.fetchrow_tenant(
        tenant_id,
        "select learning_objectives from course_versions where tenant_id = $1 and id = $2",
        version_id,
    )
    return json.loads(row["learning_objectives"])


async def _course_outline(tenant_id: str, version_id: str) -> dict:
    rows = await db.fetch_tenant(
        tenant_id,
        """select m.title as module_title, l.id as lesson_id, l.title as lesson_title
           from modules m join lessons l on l.module_id = m.id
           where m.tenant_id = $1 and m.version_id = $2
           order by m.position, l.position""",
        version_id,
    )
    return {
        "lessons": [
            {"lesson_id": str(r["lesson_id"]), "module": r["module_title"], "title": r["lesson_title"]}
            for r in rows
        ]
    }


async def _lesson_title(tenant_id: str, lesson_id: str) -> str:
    row = await db.fetchrow_tenant(
        tenant_id,
        "select title from lessons where tenant_id = $1 and id = $2", lesson_id,
    )
    return row["title"]


async def _load_blocks(tenant_id: str, lesson_id: str) -> list[dict]:
    rows = await db.fetch_tenant(
        tenant_id,
        """select id, position, block_type, content, adaptable
           from content_blocks where tenant_id = $1 and lesson_id = $2
           order by position""",
        lesson_id,
    )
    return [
        {
            "id": r["id"], "position": r["position"], "block_type": r["block_type"],
            "content": json.loads(r["content"]), "adaptable": r["adaptable"],
        }
        for r in rows
    ]


def _block_payload(b: dict) -> dict:
    return {"block_id": str(b["id"]), "block_type": b["block_type"], "content": b["content"]}


async def _copy_version_tree(tenant_id: str, source_version: str, target_version: str) -> dict[str, str]:
    """Copy modules/lessons/blocks from source to target version.
    Returns {source_lesson_id: target_lesson_id}. Blocks keep source_block_id
    lineage so rewrites and the review diff can find their canonical parent."""
    lesson_map: dict[str, str] = {}
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            modules = await conn.fetch(
                "select * from modules where tenant_id = $1 and version_id = $2 order by position",
                tenant_id, source_version,
            )
            for m in modules:
                new_module = uuid.uuid4()
                await conn.execute(
                    """insert into modules (id, tenant_id, version_id, title, position)
                       values ($1, $2, $3, $4, $5)""",
                    new_module, tenant_id, target_version, m["title"], m["position"],
                )
                lessons = await conn.fetch(
                    "select * from lessons where tenant_id = $1 and module_id = $2 order by position",
                    tenant_id, m["id"],
                )
                for l in lessons:
                    new_lesson = uuid.uuid4()
                    lesson_map[str(l["id"])] = str(new_lesson)
                    await conn.execute(
                        """insert into lessons (id, tenant_id, module_id, title, position, estimated_minutes)
                           values ($1, $2, $3, $4, $5, $6)""",
                        new_lesson, tenant_id, new_module, l["title"], l["position"], l["estimated_minutes"],
                    )
                    await conn.execute(
                        """insert into content_blocks
                             (tenant_id, lesson_id, position, block_type, content, adaptable, source_block_id)
                           select tenant_id, $3, position, block_type, content, adaptable, id
                           from content_blocks
                           where tenant_id = $1 and lesson_id = $2""",
                        tenant_id, l["id"], new_lesson,
                    )
    return lesson_map
