# CLAUDE.md — project context for Claude Code

## What this is
Refract (working name): an audience-adaptive course platform. Courses are
stored as structured content blocks in Postgres (Supabase); an LLM pipeline
"refracts" one canonical course into audience-specific versions; humans
review every AI-adapted block before publish; published versions render in
a Next.js player and export as SCORM 1.2 packages. The SCORM export shipped
and passed SCORM Cloud acceptance in August 2026 (clean import, Passed with
score reported, mid-course resume).

## Layout — TWO stacks in ONE repo
- Repo root = Python backend (FastAPI). `app/` is PYTHON (services,
  routers, workers). Python commands + git run from the root, inside
  `.venv` (`source .venv/bin/activate`).
- `web/` = Next.js frontend (App Router, TypeScript, Tailwind v4).
  npm commands run inside `web/` only. `web/app/` is Next's app dir —
  do not confuse with the Python `app/`.
- Next.js 16 has breaking changes from earlier versions: APIs,
  conventions, and file structure may all differ from model training
  data. Read the relevant guide in `web/node_modules/next/dist/docs/`
  before writing frontend code, and heed deprecation notices.

## Architecture rules
- Frontend talks to Supabase DIRECTLY under RLS (policies in migrations
  0003/0004). The FastAPI service connects to Postgres directly via
  `DATABASE_URL` as the `postgres` role, which bypasses RLS — there is no
  Supabase API key on the backend. So every backend query MUST be
  tenant-scoped: use the `db.*_tenant` helpers, which assert a tenant_id
  predicate.
- The adaptation pipeline (PLAN → REWRITE → VERIFY → human review) lives
  in `app/services/`. Nothing publishes without human review: the publish
  endpoint refuses (409) while block_revisions are pending. Never weaken
  this gate.
- Job queue = `adaptation_jobs` table claimed with FOR UPDATE SKIP LOCKED
  by an in-process worker (`app/workers/runner.py`). No Redis/Celery.
- Block content shapes are documented in the comments of
  `supabase/migrations/0001_initial.sql` and `0002_interactive_blocks.sql`.
- Design tokens (colors, fonts, spectrum gradient) live in
  `web/app/globals.css`. The spectrum gradient marks AI-adapted content
  and progress — don't repurpose it decoratively.

## Hard rules
- NEVER commit `.env` or any credential. `.env.example` documents shape.
- Migrations are append-only files in `supabase/migrations/`; they are
  applied manually in the Supabase SQL editor (no CLI wiring yet). A new
  migration = a new numbered file, never edits to an applied one.
- `.env` changes require restarting uvicorn (settings are cached).
- Python is 3.12 via `.venv`; type hints use `X | None` syntax (3.10+).
- Commit messages: imperative mood ("Add…", "Fix…"), scoped to what the
  commit actually contains. Commit only files related to the task; if
  other uncommitted changes exist, say so instead of sweeping them in.
- Don't push unless explicitly asked; summarize diffs for review.
- NEVER add AI attribution to a commit in this repo — no
  `Co-Authored-By: Claude…`, no `Claude-Session:` trailer, no "Generated
  with [Claude Code]" line, no equivalent in any form. This applies to
  every commit, amend, and rebase, now and in future sessions.

## Environment quirks (hard-won)
- macOS: Homebrew Python bottles can break on non-current macOS point
  releases (libexpat symbol skew) — python.org installer is immune.
- Supabase project uses the NEW key system: publishable/secret keys +
  asymmetric JWT signing (JWKS verification in `app/deps.py`). There is
  no legacy JWT secret.
- "Auto-expose new tables" is OFF: new tables need explicit GRANTs
  (see migration 0004) in addition to RLS policies.
