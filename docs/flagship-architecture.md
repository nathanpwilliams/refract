# Flagship Canonical — "AI at Work" Course Architecture v1

**Product:** the catalog flagship. One canonical (~2 hours), refracted
into four role tracks. Learner-facing name per track, e.g. "AI at Work
for Managers." Compliance framing: role-appropriate AI literacy with
documented completion — the shape Article 4 and internal policy buyers
need — without the course ever mentioning compliance machinery.

**Strategy note:** the proven seed course (2 modules, 4 lessons, ~54 min)
is the skeleton — its content survives as the core of Modules 1–3,
expanded and joined by a fourth module. We are not starting from zero;
we are growing tested material.

## Learning objectives (canonical; immutable through refraction)
1. Distinguish realistic AI capabilities from limitations in
   professional work
2. Describe how large language models generate output and why they can
   be confidently wrong
3. Construct effective prompts for document-centric professional tasks
4. Apply verification practices before relying on AI-generated output
5. Identify information that must never be entered into unapproved AI
   tools, and the red-line tasks AI must never perform
6. Follow your organization's AI policy: approved tools, documentation
   of use, and when to escalate to a human decision-maker

## Module map (4 modules · 12 lessons · ~115 min canonical)

### M1 — What AI actually is (obj 1, 2) — ~28 min
1.1 A tool, not a colleague (8) — capability spectrum; where AI helps,
    where it quietly fails. [seed 1.1 expanded] · knowledge_check
1.2 How language models actually work (10) — prediction not lookup;
    hallucination; confidence ≠ accuracy. [seed 1.2 + locked
    definitions block] · knowledge_check
1.3 What this means for your work (10) — NEW. Triage habit: draft vs
    decide vs verify; three worked mini-cases judging task fit ·
    scenario

### M2 — Working with AI well (obj 3) — ~30 min
2.1 Context, task, format (10) — the three-ingredient prompt.
    [seed 2.1 expanded] · knowledge_check
2.2 Iterate, don't restart (8) — steering drafts; when to abandon ·
    knowledge_check
2.3 Ground it in your documents (12) — NEW emphasis lesson. Paste the
    source; ask against the text; traceability as a habit · scenario
    (the deadline-summary scenario, relocated here)

### M3 — Judgment, risk, and red lines (obj 4, 5) — ~32 min
3.1 Verify before you rely (10) — the three-part gate: facts, judgment,
    confidentiality. [seed 2.2 expanded] · knowledge_check
3.2 What never goes in (10) — NEW. Confidential, personal, privileged,
    and regulated information; approved vs unapproved tools; the
    "would you email this to a stranger?" test · scenario
33. Red lines (12) — NEW. Tasks AI must never perform in your role
    family: final numbers, safety-critical content, legal/records
    attestations; accountability for what you sign ·
    scenario (adaptable=false core red-line statement block)

### M4 — AI at your organization (obj 6, synthesis) — ~25 min
4.1 Your policy, your tools (8) — NEW. Reading an AI policy; approved-
    tool lists; documenting use when required; escalation paths ·
    knowledge_check
4.2 Putting it together (7) — NEW. One end-to-end worked scenario:
    a realistic weekly task done right, start to finish · scenario
4.3 Final assessment (10) — quiz: 12 questions, 2 per objective,
    scenario-stemmed, pass 80%

## Interaction inventory
5 knowledge_checks · 5 scenarios · 1 final quiz (12 q) → interaction
every ~3.5 min. Meets rubric pacing line.

## Role audience profiles (refraction targets)
- **leadership** — executives/owners. Decisions, risk exposure,
  policy-setting; examples: board memos, financial summaries, vendor
  claims. Tone: peer, concise.
- **managers** — team/project managers. Delegation-era judgment:
  reviewing AI-assisted work from others; examples: status reports,
  performance notes, meeting synthesis.
- **office-professionals** — analysts, coordinators, admin, finance,
  HR. Highest-volume users; examples: correspondence, summaries,
  spreadsheets-adjacent documents, data handling.
- **field-frontline** — operations, site, service, production staff.
  Mobile-first, time-poor; examples: daily logs, checklists, incident
  notes (with the hardest red-line emphasis), voice-to-text workflows.

Vertical editions (AEC, healthcare-admin, etc.) remain FUTURE
refractions layered on these role tracks when demand data picks them.

## Authoring cadence (proposed)
1. Approve/edit this architecture (Nathan)
2. Draft lesson-by-lesson in strategy sessions: Claude drafts against
   the rubric → Nathan reviews/edits as author of record → each lesson
   locks
3. After M1 locks (3 lessons): ingest via a canonical-v2 seed script
   (Claude Code task), run ONE role refraction, review — early signal
   on how the pipeline handles fresh material before authoring M2–M4
4. Repeat per module; full 4-role refraction + timed reviews at the end
5. Definition of done: canonical + 4 tracks published, all
   rubric-passing, review-minutes metric recorded per track
