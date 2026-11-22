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


@router.get("/jobs/{job_id}/evidence")
async def job_evidence(job_id: str, caller: Caller = Depends(require_author)):
    """The pipeline v2 warranty evidence for one run.

    The data layer of the compliance file: which models ran, which prompt
    versions, the touch rate, every deterministic check result, the merged
    findings, the dispositions, and a sha256 over the canonical blocks the
    run was derived from. Populated when the job reaches 'done' or
    'halted'. PDF rendering is a later task.
    """
    row = await db.fetchrow_tenant(
        caller.tenant_id,
        """select status, error, evidence, profile_snapshot, plan
           from adaptation_jobs where tenant_id = $1 and id = $2""",
        job_id,
    )
    if row is None:
        raise HTTPException(404, "Job not found")
    if row["evidence"] is None:
        raise HTTPException(409, f"Job has no evidence yet (status {row['status']})")
    return {
        "job_id": job_id,
        "status": row["status"],
        "error": row["error"],
        "evidence": json.loads(row["evidence"]),
        "profile_snapshot": json.loads(row["profile_snapshot"] or "null"),
        "plan": json.loads(row["plan"] or "null"),
    }


@router.get("/jobs/{job_id}/revisions")
async def list_revisions(job_id: str, caller: Caller = Depends(require_author)):
    rows = await db.fetch_tenant(
        caller.tenant_id,
        """select id, block_id, original, generated, verifier_notes, review_status,
                  reviewer, disposition_reason
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
            "reviewer": r["reviewer"],
            "disposition_reason": r["disposition_reason"],
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
        """select block_id, original, reviewer from block_revisions
           where tenant_id = $1 and id = $2""",
        revision_id,
    )
    if rev is None:
        raise HTTPException(404, "Revision not found")
    if rev["reviewer"] == "pipeline_v2":
        # Pipeline v2 runs have no human review path: a machine-disposed
        # block belongs to a version that either published or halted, and a
        # halted version is fixed by re-running, never by editing it.
        raise HTTPException(
            409, "Revision was disposed by pipeline_v2; re-run the refraction "
                 "instead of editing the generated version")

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
    version = await db.fetchrow_tenant(
        caller.tenant_id,
        "select status from course_versions where tenant_id = $1 and id = $2",
        version_id,
    )
    if version is None:
        raise HTTPException(404, "Version not found")
    if version["status"] == "halted":
        # A halted version failed the v2 warranty. It is a diagnostic
        # artifact; publishing it by hand would route around the only gate
        # this pipeline has.
        raise HTTPException(
            409, "Version halted by the pipeline's warranty checks — see the "
                 "job's evidence, fix the canonical/profile/prompts, and re-run")

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
