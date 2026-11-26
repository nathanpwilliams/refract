"""Adaptation pipeline orchestration — v2: sparse refraction + warranty chain.

run_job() drives one adaptation_jobs row through:

    planning -> rewriting -> checking -> verifying -> [fallback]
             -> disposing -> done (version auto-published) | halted

Design principle: THE CANONICAL IS HUMAN-WARRANTED ONCE; EVERY REFRACTION
IS A MACHINE-PROVEN BOUNDED TRANSFORMATION OF IT. There is no human review
state in this flow — the guarantees that used to be a reviewer's job are
carried by, in order of strength:

  1. sparsity      — PLAN emits a touch list; untouched blocks are copied
                     with no model contact at all, so most of the version
                     is canonical by construction.
  2. checks.py     — six deterministic checks, computed not judged, with
                     perfect recall over what they cover. Any failure halts.
  3. verifiers.py  — VERIFY twice on two models; a blocker from either halts.
  4. disposition.py— AI review of each touched block whose null preference
                     is the canonical: in doubt, revert.

Failures are handled PER BLOCK, not per run (§6). A touched block that
fails a deterministic check or draws a blocker from either verifier is
reverted to canonical IN CODE — not by asking the disposition stage what
it thinks — and the version publishes with the adaptations that survive.
Reverting is verified like everything else: the version is read back and
every reverted block must be byte-identical to its canonical.

The run HALTS only for what a reversion cannot cure: an untouched or
locked block that failed integrity, a structure_integrity failure, a
reverted block that is not byte-identical, zero surviving adaptations, or
a halt disposition (the stage's way of saying reversion is not enough). A
halted version is a diagnostic artifact, not a review queue: you fix it by
changing the canonical, the profile, or the prompts and re-running — never
by editing the halted version.

The human review endpoints and /review UI are untouched and still work;
they are kept for the future client_sme tier. v2 never writes
review_status='pending', so the publish endpoint's 409 gate stays intact
rather than being weakened — this flow simply satisfies it.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app import db
from app.config import get_settings
from app.services import checks
from app.services import disposition as dispo
from app.services import llm
from app.services import prompts
from app.services import verifiers

log = logging.getLogger("pipeline")

PIPELINE_VERSION = "2"

# Characters of each block's prose shown to PLAN. PLAN has to judge whether
# a block carries an audience-specific example, which it cannot do from a
# block type alone; a preview is enough and keeps the prompt small.
PREVIEW_CHARS = 240

# Touching more than this fraction of a course is logged as a planning
# smell — v2's premise is that a role profile warrants a minority of
# blocks. It is a signal in the evidence, not a halt condition.
EXPECTED_MAX_TOUCH_RATE = 0.5


@dataclass
class Touched:
    """One block PLAN selected, carried through every later stage."""
    block: checks.BlockView           # canonical block
    reason: str
    instruction: str
    adapted: dict = field(default_factory=dict)
    generated: dict = field(default_factory=dict)
    unchanged: bool = True
    reverted_by: str | None = None    # 'fallback' | 'disposition' | None

    @property
    def block_id(self) -> str:
        return self.block.block_id


@dataclass
class LessonRun:
    lesson_id: str                    # canonical lesson
    target_lesson_id: str
    title: str
    blocks: list[checks.BlockView]
    touched: list[Touched] = field(default_factory=list)


async def run_job(job: dict) -> None:
    settings = get_settings()
    tenant_id = str(job["tenant_id"])
    job_id = str(job["id"])
    source_version = str(job["source_version_id"])
    target_version = str(job["target_version_id"])

    # Built first: a misconfigured VERIFY_MODEL_* should fail before PLAN and
    # REWRITE have spent anything, not after.
    verifier_list = verifiers.build_verifiers()

    profile = await _load_audience_profile(tenant_id, str(job["audience_profile_id"]))
    await _snapshot_profile(tenant_id, job_id, profile)
    objectives = await _load_objectives(tenant_id, source_version)

    # ---- Stage 1: PLAN -> touch list -----------------------------------
    await _set_status(tenant_id, job_id, "planning")
    lessons = await _load_lessons(tenant_id, source_version, canonical=True)
    plan = await llm.complete_json(
        prompts.PLAN_SYSTEM,
        prompts.PLAN_USER_TEMPLATE.format(
            audience_profile_json=json.dumps(profile),
            course_outline_json=json.dumps(_inventory(lessons)),
        ),
        temperature=settings.plan_temperature,
    )
    touch, locked_touched, plan_warnings = _resolve_touch_list(plan, lessons)
    await db.execute_tenant(
        tenant_id,
        "update adaptation_jobs set plan = $2 where tenant_id = $1 and id = $3",
        json.dumps(plan), job_id,
    )

    # ---- Copy the canonical tree, then rewrite ONLY touched blocks -----
    await _set_status(tenant_id, job_id, "rewriting")
    lesson_map = await _copy_version_tree(tenant_id, source_version, target_version)
    for lesson in lessons:
        lesson.target_lesson_id = lesson_map.get(lesson.lesson_id, "")
        lesson.touched = [
            Touched(block=b, reason=t["reason"], instruction=t["adaptation_instruction"])
            for b in lesson.blocks
            if (t := touch.get(lesson.lesson_id, {}).get(b.block_id))
        ]

    total_blocks = sum(len(l.blocks) for l in lessons)
    touched_blocks = sum(len(l.touched) for l in lessons)
    touch_rate = (touched_blocks / total_blocks) if total_blocks else 0.0
    log.info("touch rate %.1f%% (%d/%d blocks) for profile %r",
             touch_rate * 100, touched_blocks, total_blocks, profile.get("name"))
    if touch_rate > EXPECTED_MAX_TOUCH_RATE:
        log.warning("touch rate above %.0f%% — PLAN may be rewriting rather "
                    "than refracting", EXPECTED_MAX_TOUCH_RATE * 100)
    if touched_blocks == 0:
        # Legitimate (the profile warranted nothing) but worth seeing: it
        # publishes a version byte-identical to the canonical.
        log.warning("PLAN touched no blocks; this version will be a verbatim "
                    "copy of the canonical")

    for lesson in lessons:
        if lesson.touched:
            await _rewrite_lesson(tenant_id, lesson, profile, objectives, settings)

    # ---- Stage 3: deterministic checks, read back from the DB ----------
    # Read the target version from the database rather than trusting the
    # in-memory writes: the warranty is a claim about what is stored.
    await _set_status(tenant_id, job_id, "checking")
    canonical_blocks = [b for l in lessons for b in l.blocks]
    adapted_lessons = await _load_lessons(tenant_id, target_version, canonical=False)
    adapted_blocks = [b for l in adapted_lessons for b in l.blocks]
    outcome = checks.run_checks(
        canonical=canonical_blocks,
        adapted=adapted_blocks,
        touched_ids={t.block_id for l in lessons for t in l.touched},
        profile=profile,
        plan_touched_locked=locked_touched,
    )
    check_failures_by_block = _findings_from_checks(outcome)

    # ---- Stage 4: VERIFY x2 (two models) -------------------------------
    await _set_status(tenant_id, job_id, "verifying")
    findings_by_lesson: dict[str, list[dict]] = {}
    for lesson in lessons:
        if not lesson.touched:
            continue
        pairs = [
            {
                "block_id": t.block_id,
                "block_type": t.block.block_type,
                "adaptation_instruction": t.instruction,
                "original": t.block.content,
                "adapted": t.adapted,
            }
            for t in lesson.touched
        ]
        batches = [await v.verify(objectives, pairs) for v in verifier_list]
        findings_by_lesson[lesson.lesson_id] = verifiers.merge_findings(batches)

    all_findings = [f for fs in findings_by_lesson.values() for f in fs]

    # ---- Stage 5: per-block fallback, in code --------------------------
    # Every defect that a reversion can cure is cured here, by the pipeline,
    # before any model is asked for an opinion about it. A blocked block
    # becomes canonical again; the version keeps the adaptations that
    # survive. What a reversion cannot cure is collected for the halt.
    touched_ids = {t.block_id for l in lessons for t in l.touched}
    reverts, integrity_halts, unattributed = _triage(outcome, all_findings, touched_ids)
    for lesson in lessons:
        for t in lesson.touched:
            if t.block_id not in reverts:
                continue
            t.reverted_by = dispo.FALLBACK
            if not t.unchanged:
                await _write_block_content(tenant_id, lesson.target_lesson_id,
                                           t.block_id, t.block.content)
    if reverts:
        log.info("fallback reverted %d of %d adaptations", len(reverts),
                 touched_blocks)

    # ---- Stage 6: DISPOSITION on the survivors -------------------------
    await _set_status(tenant_id, job_id, "disposing")
    all_dispositions: list[dispo.Disposition] = [
        dispo.fallback_revert(block_id, causes)
        for block_id, causes in reverts.items()
    ]
    for lesson in lessons:
        if not any(t.reverted_by is None for t in lesson.touched):
            continue
        decided = await dispo.dispose_lesson(
            lesson_title=lesson.title,
            objectives=objectives,
            profile=profile,
            items=[
                {
                    "block_id": t.block_id,
                    "block_type": t.block.block_type,
                    "adaptation_instruction": t.instruction,
                    "canonical": t.block.content,
                    "adapted": t.adapted,
                    "unchanged": t.unchanged,
                }
                for t in lesson.touched if t.reverted_by is None
            ],
            findings=findings_by_lesson.get(lesson.lesson_id, []),
        )
        for t in lesson.touched:
            if t.reverted_by is not None:
                continue
            d = decided.get(t.block_id) or dispo.normalize({}, t.block_id)
            all_dispositions.append(d)
            if d.decision != dispo.ACCEPT:
                t.reverted_by = dispo.STAGE

    # ---- Record every touched block, however it was decided ------------
    by_block = {d.block_id: d for d in all_dispositions}
    for lesson in lessons:
        for t in lesson.touched:
            d = by_block.get(t.block_id)
            if d is None:
                continue
            await _record_disposition(
                tenant_id, job_id, lesson, t, d,
                notes=[f for f in findings_by_lesson.get(lesson.lesson_id, [])
                       if str(f.get("block_id")) == t.block_id]
                      + check_failures_by_block.get(t.block_id, []),
            )

    # ---- Stage 7: final read-back, then publish or halt ----------------
    # One more read from storage: it proves every reversion took, and it is
    # where the surviving-adaptation count comes from. Counting what the
    # pipeline believes it wrote would defeat the point.
    final_blocks = [b for l in await _load_lessons(tenant_id, target_version,
                                                   canonical=False)
                    for b in l.blocks]
    final_by_id = {b.block_id: b for b in final_blocks}
    reverted_ids = {t.block_id for l in lessons for t in l.touched
                    if t.reverted_by is not None}
    revert_results = checks.check_reverted(
        canonical=canonical_blocks, adapted=final_blocks,
        reverted_ids=reverted_ids)
    outcome.results.extend(revert_results)

    surviving = [
        t.block_id for l in lessons for t in l.touched
        if t.block_id in final_by_id
        and (checks.canonical_form(final_by_id[t.block_id].content)
             != checks.canonical_form(t.block.content))
    ]
    halt_dispositions = [d for d in all_dispositions if d.decision == dispo.HALT]
    halt_causes = _halt_causes(
        integrity_halts=integrity_halts,
        revert_failures=[r for r in revert_results if not r.passed],
        planned=touched_blocks,
        surviving=len(surviving),
        halt_dispositions=halt_dispositions,
    )

    evidence = {
        "pipeline_version": PIPELINE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "models": {
            "plan": settings.adaptation_model,
            "rewrite": settings.adaptation_model,
            "disposition": settings.adaptation_model,
            **{v.spec.label: f"{v.spec.provider}:{v.spec.model}" for v in verifier_list},
        },
        "prompt_versions": prompts.PROMPT_VERSIONS,
        "touch_rate": {
            "touched": touched_blocks,
            "total": total_blocks,
            "rate": round(touch_rate, 4),
            # A list, not a title-keyed map: two lessons may share a title,
            # and an evidence file must not quietly merge them.
            "by_lesson": [
                {"lesson_id": l.lesson_id, "title": l.title,
                 "touched": len(l.touched), "total": len(l.blocks)}
                for l in lessons
            ],
        },
        "plan_warnings": plan_warnings,
        "adaptations": {
            "planned": touched_blocks,
            "surviving": len(surviving),
            "reverted": len(reverted_ids),
            "reverted_blocks": [
                {"block_id": d.block_id, "reverted_by": d.decided_by,
                 "causes": reverts.get(d.block_id) or [d.reason]}
                for d in all_dispositions if d.decision != dispo.ACCEPT
            ],
        },
        "checks": outcome.summary(),
        "findings": verifiers.findings_summary(all_findings),
        "unattributed_blockers": unattributed,
        "dispositions": dispo.summary(all_dispositions),
        "canonical": {
            "version_id": source_version,
            "block_count": len(canonical_blocks),
            "block_sha256": checks.canonical_digest(canonical_blocks),
        },
        "outcome": "halted" if halt_causes else "published",
        "halt_causes": halt_causes,
    }

    if halt_causes:
        await _halt(tenant_id, job_id, target_version, halt_causes, evidence)
        log.warning("job %s HALTED: %s", job_id, "; ".join(halt_causes))
    else:
        await _publish(tenant_id, job_id, target_version, evidence)
        log.info("job %s published version %s (touch rate %.1f%%, %d accepted, "
                 "%d reverted)", job_id, target_version, touch_rate * 100,
                 evidence["dispositions"]["accepted"],
                 evidence["dispositions"]["reverted"])


# ------------------------------------------------------------- stage helpers

def _rewrite_payload(t: Touched) -> dict:
    """One block as REWRITE sees it.

    The word budget is handed over as computed numbers rather than left as a
    percentage the model has to estimate: the first live run overran +/-30%
    on five of nine touched blocks while faithfully following its
    instructions, because "within 30%" is not something a model measures
    mid-sentence.
    """
    words = checks.word_count(t.block.content)
    low, high = checks.length_bounds(words)
    return {
        "block_id": t.block_id,
        "block_type": t.block.block_type,
        "adaptation_instruction": t.instruction,
        "canonical_word_count": words,
        "allowed_word_count": {"min": low, "max": high},
        "content": t.block.content,
    }


async def _rewrite_lesson(tenant_id: str, lesson: LessonRun, profile: dict,
                          objectives: list, settings) -> None:
    """REWRITE one lesson's touched blocks and store the adapted content."""
    rewrite = await llm.complete_json(
        prompts.REWRITE_SYSTEM,
        prompts.REWRITE_USER_TEMPLATE.format(
            audience_profile_json=json.dumps(profile),
            objectives_json=json.dumps(objectives),
            lesson_title=lesson.title,
            blocks_json=json.dumps([_rewrite_payload(t) for t in lesson.touched]),
        ),
        temperature=settings.rewrite_temperature,
    )
    generated = {
        str(r["block_id"]): r
        for r in (rewrite.get("blocks") or [])
        if isinstance(r, dict) and r.get("block_id")
    }

    for t in lesson.touched:
        gen = generated.get(t.block_id)
        if gen is None:
            # REWRITE dropped the block. Treat as "unchanged": the canonical
            # already stands, and inventing content here would be the one
            # thing the warranty forbids.
            t.adapted = t.block.content
            t.unchanged = True
            t.generated = {"unchanged": True, "content": None,
                           "reason": "REWRITE returned no entry for this block"}
            continue
        content = gen.get("content")
        if gen.get("unchanged") or not isinstance(content, dict):
            t.adapted = t.block.content
            t.unchanged = True
        else:
            t.adapted = content
            t.unchanged = checks.canonical_form(content) == checks.canonical_form(t.block.content)
        t.generated = gen
        if not t.unchanged:
            await _write_block_content(tenant_id, lesson.target_lesson_id,
                                       t.block_id, t.adapted)


async def _record_disposition(tenant_id: str, job_id: str, lesson: LessonRun,
                              t: Touched, d: dispo.Disposition,
                              notes: list[dict]) -> None:
    """Apply a disposition to the version and record it as a block_revision."""
    if d.decision != dispo.ACCEPT and not t.unchanged:
        # revert_to_canonical, and halt: a halted version must not carry a
        # change nobody vouched for.
        await _write_block_content(tenant_id, lesson.target_lesson_id,
                                   t.block_id, t.block.content)

    await db.execute_tenant(
        tenant_id,
        """insert into block_revisions
             (tenant_id, job_id, block_id, original, generated, verifier_notes,
              review_status, reviewer, disposition_reason, reviewed_at)
           values ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())""",
        job_id, t.block_id,
        json.dumps(t.block.content),
        json.dumps({**t.generated, "adaptation_instruction": t.instruction,
                    "plan_reason": t.reason}),
        json.dumps(notes),
        d.review_status, dispo.REVIEWER, d.reason,
    )


def _findings_from_checks(outcome: checks.CheckOutcome) -> dict[str, list[dict]]:
    """Check failures in finding shape, so the review UI renders them too."""
    out: dict[str, list[dict]] = {}
    for r in outcome.failures:
        if r.block_id is None:
            continue
        out.setdefault(r.block_id, []).append({
            "block_id": r.block_id,
            "issue": r.check,
            "severity": "blocker",
            "detail": r.detail,
            "source": "checks",
        })
    return out


def _triage(outcome: checks.CheckOutcome, findings: list[dict],
            touched_ids: set[str]) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """Sort every defect into what a reversion cures and what it doesn't.

    Returns (reverts, integrity_halts, unattributed_blockers).

    A failure is revertible only when it is a claim about ONE TOUCHED
    BLOCK'S adapted prose. Anything else — a version-level failure, a
    failure on a block nobody touched, or any of the HALTING_CHECKS —
    describes the version rather than a block, and reverting a block would
    leave the claim just as true.
    """
    reverts: dict[str, list[str]] = {}
    integrity_halts: list[str] = []

    for r in outcome.failures:
        cause = f"{r.check}: {r.detail}"
        if (r.block_id is None
                or r.block_id not in touched_ids
                or r.check in checks.HALTING_CHECKS):
            integrity_halts.append(cause)
        else:
            reverts.setdefault(r.block_id, []).append(cause)

    unattributed: list[str] = []
    for f in verifiers.blockers(findings):
        block_id = str(f.get("block_id"))
        cause = (f"{f.get('source')}/{f.get('issue')}: {f.get('detail')}")
        if block_id in touched_ids:
            reverts.setdefault(block_id, []).append(cause)
        else:
            # The verifiers only ever saw touched pairs, so a blocker on an
            # id that is not one of them describes nothing in the version.
            # Recorded, not acted on: halting on a model's typo would be a
            # worse failure mode than ignoring it.
            unattributed.append(f"{cause} (block_id {block_id} was not touched)")
    return reverts, integrity_halts, unattributed


def _halt_causes(*, integrity_halts: list[str],
                 revert_failures: list[checks.CheckResult],
                 planned: int, surviving: int,
                 halt_dispositions: list[dispo.Disposition]) -> list[str]:
    """The four conditions a per-block reversion cannot answer, plus one.

    §6: halt when an untouched or locked block fails integrity, when
    structure_integrity fails, when a reverted block is not byte-identical
    to canonical, or when zero adaptations survive. A halt DISPOSITION is
    kept as a halt cause because that decision means precisely "reverting
    does not fix this" — the one thing the fallback cannot absorb.
    """
    causes: list[str] = []
    if integrity_halts:
        causes.append(
            f"{len(integrity_halts)} integrity failure(s) a reversion cannot "
            f"cure (first: {integrity_halts[0]})")
    if revert_failures:
        causes.append(
            f"{len(revert_failures)} reverted block(s) not byte-identical to "
            f"canonical (first: {revert_failures[0].detail})")
    if surviving == 0:
        causes.append(
            "no adaptations survived: PLAN touched no blocks"
            if planned == 0 else
            f"no adaptations survived: all {planned} were reverted")
    if halt_dispositions:
        causes.append(
            f"{len(halt_dispositions)} halt disposition(s) "
            f"(first: block {halt_dispositions[0].block_id}: "
            f"{halt_dispositions[0].reason})")
    return causes


async def _publish(tenant_id: str, job_id: str, version_id: str,
                   evidence: dict) -> None:
    await db.execute_tenant(
        tenant_id,
        """update course_versions set status = 'published', published_at = now()
           where tenant_id = $1 and id = $2""",
        version_id,
    )
    await db.execute_tenant(
        tenant_id,
        """update adaptation_jobs set status = 'done', evidence = $2, error = null
           where tenant_id = $1 and id = $3""",
        json.dumps(evidence), job_id,
    )


async def _halt(tenant_id: str, job_id: str, version_id: str,
                causes: list[str], evidence: dict) -> None:
    await db.execute_tenant(
        tenant_id,
        "update course_versions set status = 'halted' where tenant_id = $1 and id = $2",
        version_id,
    )
    await db.execute_tenant(
        tenant_id,
        """update adaptation_jobs set status = 'halted', evidence = $2, error = $3
           where tenant_id = $1 and id = $4""",
        json.dumps(evidence), "; ".join(causes)[:2000], job_id,
    )


# ------------------------------------------------------------- plan plumbing

def _preview(content: dict) -> str:
    text = " ".join(checks.prose_strings(content)).strip()
    text = " ".join(text.split())
    return text if len(text) <= PREVIEW_CHARS else text[:PREVIEW_CHARS] + "…"


def _inventory(lessons: list[LessonRun]) -> dict:
    """The course outline PLAN selects from: every block, with a preview."""
    return {
        "lessons": [
            {
                "lesson_id": l.lesson_id,
                "title": l.title,
                "blocks": [
                    {
                        "position": b.position,
                        "block_type": b.block_type,
                        "adaptable": b.adaptable,
                        "words": checks.word_count(b.content),
                        "preview": _preview(b.content),
                    }
                    for b in l.blocks
                ],
            }
            for l in lessons
        ]
    }


def _resolve_touch_list(
    plan: dict, lessons: list[LessonRun],
) -> tuple[dict[str, dict[str, dict]], set[str], list[str]]:
    """PLAN's positions -> canonical block ids.

    Returns (touch, locked_touched, warnings). A locked block PLAN selected
    is NOT sent to REWRITE — v1's invariant that adaptable=false content
    never reaches a model is preserved — but its id is returned so
    locked_integrity hard-fails the run on the planning error itself.
    """
    touch: dict[str, dict[str, dict]] = {}
    locked_touched: set[str] = set()
    warnings: list[str] = []
    by_lesson = {l.lesson_id: l for l in lessons}

    for entry in (plan.get("lessons") or []):
        if not isinstance(entry, dict):
            continue
        lesson_id = str(entry.get("lesson_id"))
        lesson = by_lesson.get(lesson_id)
        if lesson is None:
            warnings.append(f"PLAN named a lesson not in this version: {lesson_id}")
            continue
        by_position = {b.position: b for b in lesson.blocks}

        for item in (entry.get("touch_list") or []):
            if not isinstance(item, dict):
                continue
            try:
                position = int(item.get("block_position"))
            except (TypeError, ValueError):
                warnings.append(
                    f"PLAN gave a non-numeric block_position in lesson "
                    f"{lesson.title!r}: {item.get('block_position')!r}")
                continue
            block = by_position.get(position)
            if block is None:
                warnings.append(
                    f"PLAN selected position {position} in lesson "
                    f"{lesson.title!r}, which has no such block")
                continue
            if not block.adaptable:
                locked_touched.add(block.block_id)
                warnings.append(
                    f"PLAN selected LOCKED block at position {position} in "
                    f"lesson {lesson.title!r}; not rewritten, run will halt")
                continue
            instruction = (item.get("adaptation_instruction") or "").strip()
            reason = (item.get("reason") or "").strip()
            if not instruction and not reason:
                warnings.append(
                    f"PLAN selected position {position} in lesson "
                    f"{lesson.title!r} with no reason or instruction; skipped")
                continue
            touch.setdefault(lesson_id, {})[block.block_id] = {
                "reason": reason,
                "adaptation_instruction": instruction or reason,
            }
    return touch, locked_touched, warnings


# ---------------------------------------------------------------- db helpers

async def _set_status(tenant_id: str, job_id: str, status: str) -> None:
    await db.execute_tenant(
        tenant_id,
        "update adaptation_jobs set status = $2 where tenant_id = $1 and id = $3",
        status, job_id,
    )


async def _snapshot_profile(tenant_id: str, job_id: str, profile: dict) -> None:
    """Freeze the audience attributes this run used.

    Profiles are versioned content that authors keep editing; evidence that
    pointed at a live profile row would describe the wrong inputs the moment
    someone revised it.
    """
    await db.execute_tenant(
        tenant_id,
        """update adaptation_jobs set profile_snapshot = $2
           where tenant_id = $1 and id = $3""",
        json.dumps(profile), job_id,
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


async def _load_lessons(tenant_id: str, version_id: str, *,
                        canonical: bool) -> list[LessonRun]:
    """Lessons + blocks for one version, in course order.

    For an adapted version, BlockView.block_id is the block's
    source_block_id — the canonical lineage key every stage pairs on.
    """
    lesson_rows = await db.fetch_tenant(
        tenant_id,
        """select l.id as lesson_id, l.title
           from modules m join lessons l on l.module_id = m.id
           where m.tenant_id = $1 and m.version_id = $2
           order by m.position, l.position""",
        version_id,
    )
    id_column = "cb.id" if canonical else "cb.source_block_id"
    block_rows = await db.fetch_tenant(
        tenant_id,
        f"""select cb.lesson_id, {id_column} as block_id, cb.position,
                   cb.block_type, cb.content, cb.adaptable
            from content_blocks cb
            join lessons l on l.id = cb.lesson_id
            join modules m on m.id = l.module_id
            where cb.tenant_id = $1 and m.version_id = $2
            order by m.position, l.position, cb.position""",
        version_id,
    )

    blocks_by_lesson: dict[str, list[checks.BlockView]] = {}
    for r in block_rows:
        blocks_by_lesson.setdefault(str(r["lesson_id"]), []).append(
            checks.BlockView(
                block_id=str(r["block_id"]),
                position=r["position"],
                block_type=r["block_type"],
                adaptable=r["adaptable"],
                content=json.loads(r["content"]),
            )
        )
    return [
        LessonRun(
            lesson_id=str(r["lesson_id"]),
            target_lesson_id="",
            title=r["title"],
            blocks=blocks_by_lesson.get(str(r["lesson_id"]), []),
        )
        for r in lesson_rows
    ]


async def _write_block_content(tenant_id: str, target_lesson_id: str,
                               source_block_id: str, content: dict) -> None:
    await db.execute_tenant(
        tenant_id,
        """update content_blocks set content = $2
           where tenant_id = $1 and lesson_id = $3 and source_block_id = $4""",
        json.dumps(content), target_lesson_id, source_block_id,
    )


async def _copy_version_tree(tenant_id: str, source_version: str,
                             target_version: str) -> dict[str, str]:
    """Copy modules/lessons/blocks from source to target version.

    Returns {source_lesson_id: target_lesson_id}. Blocks keep source_block_id
    lineage so rewrites, checks, and the review diff can find their canonical
    parent. Untouched blocks are never rewritten after this copy — their
    bytes come from the canonical row and nothing else, which is what
    verbatim_integrity asserts.
    """
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
