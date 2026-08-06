# Build Brief — SCORM 1.2 Packager (Phase 3)

**For:** Claude Code, working in this repo.
**Read first:** `app/services/pipeline.py`, `app/routers/adaptation.py`,
`supabase/migrations/0001_initial.sql` and `0002_interactive_blocks.sql`
(block content shapes are documented in the migration comments), and
`web/components/blocks/Block.tsx` + `web/components/QuizRunner.tsx`
(the React renderers this packager mirrors in static form).

## Goal

A FastAPI endpoint that turns any **published** course version into a
downloadable, self-contained **SCORM 1.2** zip that imports into any
compliant LMS and reports completion + score. This is the delivery mode
for buyers with an existing LMS; treat compatibility as the top value.

## Deliverables

1. `app/services/scorm.py` — package builder:
   - `build_package(version_id, tenant_id) -> bytes` (zip in memory)
   - Renders the version's lessons (via existing db helpers; service
     role, tenant-scoped like everything else in `app/services/`)
2. `app/templates/scorm/` — HTML/CSS/JS assets:
   - `page.html` template (or Python string templates — builder's choice;
     no new heavy dependencies. `jinja2` is acceptable if preferred.)
   - `styles.css` — self-contained; SYSTEM FONT STACK ONLY (no webfont
     files, no external requests). Reuse the token palette from
     `web/app/globals.css` (paper/ink/line/action/accept/reject/edit
     colors + the spectrum gradient as the progress accent).
   - `scorm-api.js` — the 1.2 runtime shim (details below)
   - `app.js` — navigation + interactive blocks in vanilla JS
3. Endpoint in `app/routers/adaptation.py`:
   - `GET /adaptation/versions/{version_id}/scorm` → zip download,
     `require_author` dependency, 404 if version not found in caller's
     tenant, 409 if version status != 'published'
4. Nothing else changes. No schema changes. No frontend changes.

## Package design decisions (follow these; don't re-litigate)

- **SCORM 1.2, single SCO.** One `<organization>` with one `<item>`;
  the SCO is an index page with internal navigation across all lessons.
  Single-SCO is the maximum-compatibility shape.
- **`imsmanifest.xml`** at zip root: identifier from version id, title
  from course title + version label, `adlcp:scormtype="sco"` resource
  listing every file. Include the standard 1.2 schema declarations.
- **Runtime (scorm-api.js):**
  - API discovery: walk `window.parent` chain and `window.opener` for
    the `API` object (standard 1.2 discovery loop, max ~10 hops)
  - `LMSInitialize("")` on load; `LMSFinish("")` on `beforeunload`
  - Absent API (opened directly in a browser): run in "preview mode" —
    all functions no-op, a small badge says "Preview — not connected to
    an LMS". The package must still fully work as plain HTML.
  - Progress: store `{lesson_index, completed_lessons[], quiz}` JSON in
    `cmi.suspend_data` (respect the 1.2 limit of 4096 chars — the
    payload is tiny); set `cmi.core.lesson_location` to current lesson
    index; restore both on init for resume.
  - Status: `cmi.core.lesson_status` = `incomplete` on first launch;
    when every lesson is visited AND the final quiz is passed →
    `passed` and `cmi.core.score.raw` = quiz percentage (0–100, also
    set score.min=0, score.max=100). If no quiz exists in the course:
    `completed` when all lessons visited.
  - `LMSCommit("")` after every meaningful state change (throttle to
    max ~1/second).
- **Rendering:** mirror `Block.tsx` block-for-block in static HTML:
  headings, text (markdown → HTML at BUILD time in Python — use the
  `markdown` package or a minimal converter for bold/italic/lists/
  paragraphs; no client-side markdown parsing), callout variants,
  image placeholders w/ alt text (storage integration is out of scope),
  code, table, video as titled link + transcript in `<details>`.
- **Interactives in vanilla JS (app.js):** knowledge_check (single
  select, instant feedback, correct/incorrect styling) and scenario
  (choice → quality-coded consequence, "try another approach" on
  non-best). Quiz: same scoring rules as `QuizRunner.tsx` — exact-match
  sets for multi_select, score = correct/total, pass vs `pass_pct`,
  rationales shown after submit.
- **Accessibility floor:** semantic HTML, one `<h1>` per page, visible
  focus, keyboard-operable buttons for every interactive, WCAG AA
  contrast with the token palette, no color-only state (pair color with
  text labels like "Correct"/"Not quite").
- **Zero network calls at runtime.** Everything in the zip. No CDN, no
  fonts, no analytics, no supabase.

## Acceptance criteria (all must pass)

1. `GET .../scorm` on a published version returns a zip; on a
   non-published version returns 409; cross-tenant returns 404.
2. Unzipped package opened directly in a browser works fully in
   preview mode (navigate, interactives, quiz) with zero console errors.
3. Uploaded to SCORM Cloud: imports without manifest warnings that
   block launch; launching, completing all lessons, and passing the
   quiz results in SCORM Cloud showing **Passed** with the score;
   closing mid-course and relaunching resumes at the same lesson with
   progress intact.
4. `python -m py_compile` clean; a small pytest for
   `build_package` asserting the zip contains `imsmanifest.xml`, one
   HTML entry point, and no absolute URLs (`http://`/`https://`) in any
   asset except the manifest schema declarations.

## Workflow expectations

- Work on a branch: `scorm-packager`. Commit in logical units.
- Do not push; summarize the diff for human review when done.
- If the seeded published version is needed for a live test, the API
  runs locally (`uvicorn app.main:app --reload`) with credentials in
  `.env` — ask before running anything that costs API tokens (this
  task should need none; the packager only reads the database).
- Anything ambiguous: choose the more LMS-compatible option and note
  the decision in the final summary.
