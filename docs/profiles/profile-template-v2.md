# Audience Profile Template — v2 (rich)

Every audience profile is a steering document for the PLAN and REWRITE
stages. Target 250–400 words of content across these fields. The test
for every line: would REWRITE produce a different, better sentence
because this line exists? Cut anything that wouldn't change output.

Stored in audience_profiles.attributes (JSONB) — same schema, richer
content. Field names below are the JSON keys.

## Fields

**role_context** (60–100 words)
Who they are, what a normal day contains, what they're accountable
for, and to whom. Written as description, not bullet fragments — the
model steers better on prose.

**artifacts** (list, 6–10 items)
The named documents and outputs this role actually touches. Specific
beats generic: "weekly status report to a director" not "reports."
These become the raw material for transposed examples.

**ai_failure_modes** (list, 2–4 items)
The characteristic ways THIS role misuses AI — the mistakes their
scenarios should dramatize and their emphasis should guard against.
This field steers scenario transposition harder than any other.

**vocabulary** (prefer / avoid lists)
Terms that signal insider fluency vs. terms that mark the content as
written by an outsider or a different department.

**example_domains** (list, 3–5 items)
Settings where worked examples should live, specific enough to
generate from: "leveling two vendor quotes for a services contract."

**tone_register** (1–2 sentences)
How to speak to them: peer vs. instructor, brevity tolerance,
formality. Include what would feel condescending.

**redline_emphasis** (1–3 items)
Which of the course's red lines deserve extra weight for this role,
and why — their proximity to those risks.

**reading_context** (1–2 sentences)
Device, time slots, attention conditions. Drives sentence length and
interaction density decisions.

**prior_knowledge** (one of: novice / mixed / intermediate)
Assume the lowest common denominator of the track's real audience.

## Rules
- Nothing in a profile may contradict canonical facts or objectives —
  profiles steer flavor and emphasis, never substance.
- No industry verticals here: these four profiles are role-based.
  Vertical editions get their own profiles layered later.
- Profiles are versioned content: date-stamp changes in a trailing
  `notes` field; profile edits invalidate no existing versions but
  inform all future runs.
