# Refract

**One course, written once. Many audiences, each one earned.**

Corporate training has a structural problem: the course that works for a
frontline manager is not the course that works for an engineer or a
salesperson, but nobody can afford to author four of them. So everyone
ships one generic course and absorbs the relevance loss.

Refract is an audience-adaptive course platform. An author writes one
*canonical* course. The platform then "refracts" it into
audience-specific versions — reshaping the scenarios, worked examples,
and emphasis for a given role — while holding the things that must not
drift (learning objectives, answer keys, locked blocks) byte-identical.
Published versions render in a web player and export as standards-
compliant SCORM 1.2 packages for any corporate LMS.

Courses are not documents here. They are structured content blocks in
Postgres — text, callout, video, knowledge_check, scenario, quiz — which
is what makes per-block adaptation, per-block review, and per-block
integrity proofs possible at all.

---

## The problem the architecture is actually solving

Using an LLM to rewrite training content is easy. Using it in a context
where **someone's compliance officer has to sign off on the output** is
the hard part. Three failure modes showed up in real pipeline runs, and
the architecture is a direct response to each:

| Observed failure | Architectural answer |
| --- | --- |
| Rewriting every block produces gratuitous synonym churn on content that was already audience-neutral | **Sparse refraction** — the model must justify each block it touches |
| Per-block human review degenerates into rubber-stamping at volume | **Warranty chain** — move the guarantees humans can't reliably give onto code that can |
| Model self-assessment is not evidence | **Deterministic checks + cross-model verification + recorded provenance** |

### Pipeline stages

**PLAN → REWRITE → VERIFY → REVIEW → PUBLISH**

- **PLAN** reads the course outline and a structured *audience profile*
  (role, reading context, AI failure modes, redline emphasis) and plans
  the adaptation.
- **REWRITE** adapts blocks lesson-by-lesson at temperature 0.3.
- **VERIFY** re-reads canonical against adapted at temperature 0.0 and
  attaches findings to each pair.
- **REVIEW** surfaces original vs. generated side by side with the
  verifier's findings; a human accepts, edits, or rejects per block.
- **PUBLISH** refuses with HTTP 409 while any revision is still pending.
  That refusal *is* the guarantee — it cannot be satisfied by a model.

Jobs run through an `adaptation_jobs` table claimed with
`FOR UPDATE SKIP LOCKED` by an in-process worker. No Redis, no Celery —
at this volume the database is the queue, and it's one less moving part
to operate.

### Sparse refraction

Instead of rewriting a lesson wholesale, PLAN emits a **touch list**:
per lesson, the specific blocks to adapt, each with a reason and an
adaptation instruction. Selection is justified — scenarios, worked
examples, role-aligned emphasis — and explicitly excluded: mechanism
explanations, definitions, anything flagged `adaptable=false`.

Blocks not on the touch list are copied **verbatim, with no model
contact at all**. The surface area a reviewer has to trust shrinks to
the minority of blocks that were actually changed, and the rest is
byte-identical by construction rather than by inspection.

### Warranty chain

The design principle: **the canonical is human-warranted once; every
refraction is a machine-proven bounded transformation of it.**

A deterministic check suite — ordinary code, no model in the loop —
runs over the adapted version against canonical:

- `verbatim_integrity` — untouched blocks byte-identical
- `locked_integrity` — `adaptable=false` blocks byte-identical, even if
  PLAN erred and touched one (hard fail)
- `answer_key_integrity` — option ids, counts, and correct flags agree
  between canonical and adapted
- `length_budget` — touched blocks within a computed ±30% word bound
- `structure_integrity` — block types unchanged, nothing added, removed,
  or reordered
- `vocabulary_ban` — the profile's avoid-list terms absent

These have perfect recall for what they cover, which is the point: they
are the warranty's deterministic floor. Above that floor sit
cross-model verification (two models at temperature 0; a blocker from
either halts the run) and recorded provenance — models used, prompt
versions, touch rate, check results, and a SHA-256 over the canonical's
ordered block contents — so a published version can be traced back to
exactly what produced it.

**Build status:** stages PLAN → REWRITE → VERIFY → human review →
publish are implemented and have been run end-to-end against live
content. Sparse refraction, the deterministic check suite, cross-model
verification, and the provenance record are fully specified and are the
active build.

### Tenant isolation, from two directions

The frontend talks to Supabase **directly** under row-level security.
The FastAPI service connects to Postgres directly as the `postgres` role
and therefore bypasses RLS entirely — so every backend query goes through
`db.*_tenant` helpers that *assert* a `tenant_id` predicate is present
rather than trusting the author of the query to remember one.

### SCORM export

Published versions export as SCORM 1.2 zips: `imsmanifest.xml`, one HTML
entry point, interactives in vanilla JS, and **zero network calls at
runtime** — no CDN, no fonts, no analytics. Progress and completion are
reported through the LMS runtime API, with resume on relaunch.

**Verified against SCORM Cloud:** packages import without launch-blocking
manifest warnings, report **Passed** with the quiz score on completion,
and resume at the correct lesson after closing mid-course.

---

## Stack

| | |
| --- | --- |
| **Backend** | Python 3.12, FastAPI, asyncpg, Anthropic SDK |
| **Frontend** | Next.js 16 (App Router), React 19, TypeScript, Tailwind v4 |
| **Data** | Postgres via Supabase — RLS for the client, service role for the backend |
| **Auth** | Supabase asymmetric JWT, verified against JWKS |
| **Queue** | `adaptation_jobs` + `FOR UPDATE SKIP LOCKED`, in-process worker |
| **Export** | SCORM 1.2, generated server-side |

Migrations are append-only numbered files in `supabase/migrations/`;
block content shapes are documented in the comments of `0001_initial.sql`
and `0002_interactive_blocks.sql`.

## Running it locally

```bash
# backend (repo root)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # Supabase keys, pooled DATABASE_URL, Anthropic key
uvicorn app.main:app --reload
pytest

# frontend
cd web && npm install && npm run dev
```

Apply `supabase/migrations/*.sql` in order in the Supabase SQL editor,
then seed with `python scripts/seed.py`.

## API surface

```
POST /adaptation/jobs                        queue an adaptation
GET  /adaptation/jobs/{id}                   job status
GET  /adaptation/jobs/{id}/revisions         review feed (original vs generated)
POST /adaptation/revisions/{id}/review       accept / edit / reject
POST /adaptation/versions/{id}/publish       409 while revisions are pending
GET  /adaptation/versions/{id}/scorm         SCORM 1.2 zip (published only)
```

## A note on how this was built

This is AI-assisted development under human review. I use Claude Code
heavily — as a collaborator on implementation, not as a substitute for
deciding what gets built or for reading what lands. The architecture
decisions here are mine and I can defend any of them; the code was
written fast because of the tooling, and reviewed because fast is not
the same as correct. Commits that were co-authored say so in their
trailers.

The irony is deliberate: a platform whose whole thesis is *machine work
needs proof, not assurances* was built the same way.

## License

Source-available, all rights reserved. See [LICENSE-NOTICE](LICENSE-NOTICE).
