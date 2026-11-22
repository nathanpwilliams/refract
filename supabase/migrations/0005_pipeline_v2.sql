-- =============================================================
-- Migration 0005 — pipeline v2: sparse refraction + warranty chain
--
-- v1 ended every run at 'awaiting_review' and published only after a
-- human cleared every block_revision. v2 replaces that with a machine
-- warranty: a deterministic check suite, two independent verifiers, and
-- an AI disposition stage whose null preference is the canonical. A run
-- either auto-publishes or HALTS; there is no human state in the flow.
--
-- The human review path (API + /review UI) is NOT removed — it stays
-- for the future client_sme tier. Its review_status values survive
-- alongside the two new machine values.
--
-- What this adds:
--   1. adaptation_jobs.profile_snapshot — audience attributes as they
--      were when the job was claimed (profiles are versioned content;
--      evidence must not depend on their later edits).
--   2. adaptation_jobs.evidence — the compliance file's data layer:
--      models, prompt versions, touch rate, check results, findings,
--      dispositions, and a sha256 over the canonical blocks the run was
--      derived from. PDF rendering is a later task.
--   3. New job statuses 'checking' / 'disposing' / 'halted' for the new
--      stages, and version status 'halted' for a run that failed the
--      warranty.
--   4. block_revisions: machine review_status values, a textual
--      reviewer ('pipeline_v2' — reviewed_by is a profiles FK and
--      cannot name a machine), and the disposition's stated reason.
--
-- A halted version is a DIAGNOSTIC ARTIFACT. It is not a review queue:
-- fixing it means changing the canonical, the profile, or the prompts
-- and re-running, never editing the halted version in place.
-- =============================================================

-- ---------- 1 + 2. provenance & evidence ----------

alter table adaptation_jobs
  add column if not exists profile_snapshot jsonb,
  add column if not exists evidence         jsonb;

comment on column adaptation_jobs.profile_snapshot is
  'audience_profiles.attributes as of job claim time (frozen for evidence)';
comment on column adaptation_jobs.evidence is
  'v2 warranty evidence: models, prompt versions, touch_rate, checks, findings, dispositions, canonical digest';

-- ---------- 3. new statuses ----------

alter table adaptation_jobs
  drop constraint if exists adaptation_jobs_status_check;

alter table adaptation_jobs
  add constraint adaptation_jobs_status_check
  check (status in ('queued','planning','rewriting','checking','verifying',
                    'disposing','awaiting_review','done','halted','failed'));
-- 'awaiting_review' retained for the client_sme tier; 'failed' is an
-- unhandled error (crash, malformed model output), 'halted' is an
-- orderly warranty refusal with evidence attached.

alter table course_versions
  drop constraint if exists course_versions_status_check;

alter table course_versions
  add constraint course_versions_status_check
  check (status in ('draft','generating','generated','in_review',
                    'published','halted','archived'));

-- ---------- 4. machine dispositions on block_revisions ----------

alter table block_revisions
  add column if not exists reviewer            text,
  add column if not exists disposition_reason  text;

comment on column block_revisions.reviewer is
  'who decided: ''pipeline_v2'' for machine dispositions; null when reviewed_by (a profile) decided';
comment on column block_revisions.disposition_reason is
  'the disposition stage''s stated reason for accept / revert_to_canonical / halt';

alter table block_revisions
  drop constraint if exists block_revisions_review_status_check;

alter table block_revisions
  add constraint block_revisions_review_status_check
  check (review_status in ('pending','accepted','edited','rejected',
                           'auto_accepted','auto_reverted'));

-- v2 never writes 'pending', so the publish endpoint's 409 gate is
-- satisfied by construction rather than weakened.

create index if not exists idx_revisions_job on block_revisions (job_id);
