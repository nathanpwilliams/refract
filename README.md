# Course Engine — Week 1-2 Scaffold

Backend service for the AI training platform. Owns the adaptation pipeline
(plan → rewrite → verify → human review → publish). Course/lesson CRUD is
handled by the Next.js frontend talking to Supabase directly under RLS —
this service exists for the things the frontend can't do.

## Architecture recap

- Frontend (Next.js) ⇄ Supabase directly for CRUD, auth, and reads. RLS
  enforces tenant isolation there.
- This FastAPI service connects with the service-role key (bypasses RLS),
  so every query is explicitly tenant-scoped via the `db.*_tenant` helpers.
- Job queue is the `adaptation_jobs` table, claimed with
  `FOR UPDATE SKIP LOCKED` by an in-process worker. No Redis/Celery needed
  at MVP volume.

## Setup

1. **Create a Supabase project** at https://supabase.com (free tier is fine
   for development).

2. **Run the migration.** Either paste
   `supabase/migrations/0001_initial.sql` into the SQL editor, or use the
   CLI:

   ```bash
   npm install -g supabase
   supabase link --project-ref <your-project-ref>
   supabase db push
   ```

3. **Configure environment.**

   ```bash
   cp .env.example .env
   ```

   Fill in values from the Supabase dashboard (Settings → API for keys and
   JWT secret; Settings → Database for the pooled connection string — use
   the transaction pooler on port 6543) and your Anthropic API key from
   https://console.anthropic.com.

4. **Install and run.**

   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   uvicorn app.main:app --reload
   ```

5. **Smoke test.**

   ```bash
   curl http://localhost:8000/health
   ```

## Pipeline flow

1. `POST /adaptation/jobs` with a course_id + audience_profile_id — creates
   the target course_version and queues a job.
2. The worker claims the job: PLAN once per course, then per lesson
   REWRITE (temp 0.3) and VERIFY (temp 0.0), writing pairs into
   `block_revisions`.
3. `GET /adaptation/jobs/{id}/revisions` feeds the review UI — original vs
   generated side by side, with verifier findings attached.
4. `POST /adaptation/revisions/{id}/review` with accepted/edited/rejected.
5. `POST /adaptation/versions/{id}/publish` — refuses while any revision is
   still pending. This gate is the "human reviews everything" guarantee.

## Deliberate omissions (later weeks)

- SCORM packager (week 7) — will live in `app/services/scorm.py`.
- Quiz generation endpoint — prompts are ready in
  `app/services/prompts.py` (QUIZGEN), wire up after the player exists.
- Rate limiting / request logging middleware.
- Anthropic model + limits are env vars; check
  https://docs.claude.com/en/api/overview for current model strings.

## Testing without burning API credits

Point `complete_json` at a stub: set `llm_max_retries=0` and monkeypatch
`app.services.llm.complete_json` in tests to return canned plan/rewrite/
verify payloads. The pipeline is deterministic given those inputs.
