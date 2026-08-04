"""Pipeline endpoints. CRUD for courses/lessons lives in the frontend via
Supabase directly (RLS enforces tenancy there); this service only owns what
the frontend can't do — the LLM pipeline and, later, SCORM packaging.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app import db
from app.deps import Caller, require_author
from app.services import scorm

router = APIRouter(prefix="/adaptation", tags=["adaptation"])


class CreateJobRequest(BaseModel):
    course_id: str
    audience_profile_id: str
    label: str                      # e.g. "AEC v1"


@router.post("/jobs")
async def create_job(req: CreateJobRequest, caller: Caller = Depends(require_author)):
    source = await db.fetchrow_tenant(
        caller.tenant_id,
        """select id, learning_objectives from course_versions
           where tenant_id = $1 and course_id = $2 and is_canonical""",
        req.course_id,
    )
    if source is None:
        raise HTTPException(404, "Course has no canonical version")

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            target = await conn.fetchrow(
                """insert into course_versions
                     (tenant_id, course_id, audience_profile_id, label,
                      status, learning_objectives)
                   values ($1, $2, $3, $4, 'generating', $5)
                   returning id""",
                caller.tenant_id, req.course_id, req.audience_profile_id,
                req.label, source["learning_objectives"],
            )
            job = await conn.fetchrow(
                """insert into adaptation_jobs
                     (tenant_id, source_version_id, target_version_id, audience_profile_id)
                   values ($1, $2, $3, $4)
                   returning id, status""",
                caller.tenant_id, source["id"], target["id"], req.audience_profile_id,
            )
    return {"job_id": str(job["id"]), "target_version_id": str(target["id"]), "status": "queued"}


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, caller: Caller = Depends(require_author)):
    row = await db.fetchrow_tenant(
        caller.tenant_id,
        """select id, status, error, target_version_id, created_at, finished_at
           from adaptation_jobs where tenant_id = $1 and id = $2""",
        job_id,
    )
    if row is None:
        raise HTTPException(404, "Job not found")
    return dict(row)


@router.get("/jobs/{job_id}/revisions")
async def list_revisions(job_id: str, caller: Caller = Depends(require_author)):
    rows = await db.fetch_tenant(
        caller.tenant_id,
        """select id, block_id, original, generated, verifier_notes, review_status
           from block_revisions where tenant_id = $1 and job_id = $2""",
        job_id,
    )
    return [
        {
            "revision_id": str(r["id"]),
            "block_id": str(r["block_id"]),
            "original": json.loads(r["original"]),
            "generated": json.loads(r["generated"]),
            "verifier_notes": json.loads(r["verifier_notes"] or "[]"),
            "review_status": r["review_status"],
        }
        for r in rows
    ]


class ReviewRequest(BaseModel):
    action: str                     # accepted | edited | rejected
    edited_content: dict | None = None


@router.post("/revisions/{revision_id}/review")
async def review_revision(revision_id: str, req: ReviewRequest,
                          caller: Caller = Depends(require_author)):
    if req.action not in ("accepted", "edited", "rejected"):
        raise HTTPException(422, "action must be accepted|edited|rejected")
    if req.action == "edited" and req.edited_content is None:
        raise HTTPException(422, "edited_content required when action=edited")

    rev = await db.fetchrow_tenant(
        caller.tenant_id,
        "select block_id, original from block_revisions where tenant_id = $1 and id = $2",
        revision_id,
    )
    if rev is None:
        raise HTTPException(404, "Revision not found")

    # rejected -> restore canonical content on the adapted block;
    # edited   -> apply the author's edit
    if req.action in ("rejected", "edited"):
        new_content = (
            json.loads(rev["original"]) if req.action == "rejected" else req.edited_content
        )
        await db.execute_tenant(
            caller.tenant_id,
            """update content_blocks set content = $2
               where tenant_id = $1 and source_block_id = $3""",
            json.dumps(new_content), str(rev["block_id"]),
        )

    await db.execute_tenant(
        caller.tenant_id,
        """update block_revisions
           set review_status = $2, reviewed_by = $3, reviewed_at = now()
           where tenant_id = $1 and id = $4""",
        req.action, caller.user_id, revision_id,
    )
    return {"revision_id": revision_id, "review_status": req.action}


@router.post("/versions/{version_id}/publish")
async def publish_version(version_id: str, caller: Caller = Depends(require_author)):
    pending = await db.fetchrow_tenant(
        caller.tenant_id,
        """select count(*) as n from block_revisions br
           join adaptation_jobs j on j.id = br.job_id
           where br.tenant_id = $1 and j.target_version_id = $2
             and br.review_status = 'pending'""",
        version_id,
    )
    if pending["n"] > 0:
        raise HTTPException(409, f"{pending['n']} revisions still pending review")

    await db.execute_tenant(
        caller.tenant_id,
        """update course_versions
           set status = 'published', published_at = now()
           where tenant_id = $1 and id = $2""",
        version_id,
    )
    return {"version_id": version_id, "status": "published"}


@router.get("/versions/{version_id}/scorm")
async def download_scorm(version_id: str, caller: Caller = Depends(require_author)):
    """Export a published version as a downloadable SCORM 1.2 package."""
    row = await db.fetchrow_tenant(
        caller.tenant_id,
        "select status from course_versions where tenant_id = $1 and id = $2",
        version_id,
    )
    if row is None:
        raise HTTPException(404, "Version not found")
    if row["status"] != "published":
        raise HTTPException(409, "Version is not published")

    data = await scorm.build_package(version_id, caller.tenant_id)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="scorm-{version_id}.zip"'},
    )
