# Build Brief — Pipeline v2: Sparse Refraction + Warranty Chain

**For:** Claude Code, in this repo, on branch `pipeline-v2`.
**Read first:** CLAUDE.md · app/services/pipeline.py, prompts.py ·
scripts/ingest_canonical.py · migrations 0001/0002 (block shapes).
**Context:** five production runs proved (a) uniform rewriting produces
gratuitous synonym churn on audience-neutral blocks, (b) human per-block
review degenerated to rubber-stamping, (c) deterministic properties
(answer keys, locked blocks) are checkable in code with perfect recall.
v2 removes humans from all runs and makes the machine's guarantees
strong enough to carry the warranty. Design principle: **the canonical
is human-warranted once; every refraction is a machine-PROVEN bounded
transformation of it.**

## Architecture (replaces uniform per-lesson rewrite)

PLAN → (touch-list) → REWRITE (touched blocks only) → CHECKS
(deterministic) → VERIFY ×2 (two models) → DISPOSITION (AI review) →
auto-publish or halt. No human review state in this flow.

### 1. Sparse refraction
- PLAN prompt reworked: given course outline + audience profile, output
  a **touch_list**: per-lesson arrays of {block_position, reason,
  adaptation_instruction}. Selection rules in the prompt: touch blocks
  where the profile gives CONCRETE cause — scenarios, worked examples,
  knowledge_check/scenario framing, emphasis aligned to
  ai_failure_modes / redline_emphasis. DO NOT touch: conceptual/
  mechanism explanations, definitions, anything adaptable=false,
  headings unless an example is embedded. Expected touch rate for a
  role profile: minority of blocks; log the rate.
- Untouched blocks: copied verbatim (no model contact). REWRITE
  receives only touched blocks, each with its adaptation_instruction,
  and the profile; prompt explicitly instructs attention to
  ai_failure_modes for scenario reshaping, and states that returning
  unchanged remains acceptable if the instruction can't be honored
  without harming the block.

### 2. Deterministic check suite — code, not AI (new: app/services/checks.py)
Run on every block of the new version vs canonical:
- **verbatim_integrity**: untouched blocks byte-identical to canonical
- **locked_integrity**: adaptable=false blocks byte-identical (even if
  PLAN erred and touched them → hard fail)
- **answer_key_integrity**: for knowledge_check/scenario/questions —
  canonical and adapted agree on: option ids, option count, correct
  flags / quality ratings (compare normalized structures)
- **length_budget**: touched text-bearing blocks within ±30% word
  count (hard bound, computed, not judged)
- **structure_integrity**: block_type unchanged, content keys superset-
  compatible, no blocks added/removed/reordered
- **vocabulary_ban**: profile's avoid-list terms absent from adapted
  text (case-insensitive, word-boundary)
Output: machine-readable results list {check, block_id, pass, detail}.
Any fail → version status 'halted' (see §6). These checks are the
warranty's deterministic floor: perfect recall for what they cover.

### 3. Cross-model verification
- VERIFY runs twice at temperature 0 with two different models,
  configurable via env: VERIFY_MODEL_A (default: current adaptation
  model family's strong model) and VERIFY_MODEL_B (default: a
  different Claude model, e.g. a Haiku-class model). Design the
  verifier interface provider-agnostic so an OpenAI/Gemini verifier
  can be added later by env alone; note in docs that same-vendor
  models are only partially decorrelated and a second vendor is the
  roadmap hardening.
- Findings merged with source tags. Agreement rule: a finding severity
  'blocker' from EITHER verifier → halt. Advisory findings feed
  disposition.

### 4. Disposition stage (AI review replaces human review)
New pipeline stage + prompt: for each TOUCHED block, given canonical,
adapted, the adaptation_instruction, and all findings: output one of
- accept — genuine, warranted improvement for the audience
- revert_to_canonical — change is gratuitous (near-zero semantic
  delta), harmful, or unwarranted; canonical text is restored.
  **Null-preference is explicit in the prompt: when in doubt, the
  canonical wins.**
- halt — something is wrong that reversion doesn't fix (should be
  rare; requires stated reason)
Dispositions + reasons recorded per block in block_revisions
(review_status values: 'auto_accepted' | 'auto_reverted'; reviewer
field: 'pipeline_v2'). The existing human-review API/UI remains
functional for the future client_sme tier — do not remove it.

### 5. Provenance & evidence (migration 0005)
- adaptation_jobs new columns: profile_snapshot jsonb (attributes at
  claim time), evidence jsonb.
- evidence written at completion: {pipeline_version, models used
  (plan/rewrite/verify_a/verify_b/disposition), prompt_version ids,
  touch_rate, checks results summary, findings summary, dispositions
  summary, canonical_version_id + a sha256 over its ordered block
  contents}. This is the compliance file's data layer; PDF rendering
  is a later task.

### 6. Publish semantics
- All checks pass + no blocker findings + no halt dispositions →
  version auto-publishes (status 'published', published_at now).
- Otherwise → status 'halted' (add to version status constraint),
  job.error summarizing causes. Halted versions are diagnostic
  artifacts — fixing means changing canonical/profile/prompts and
  re-running, not editing the version. No notification queue, no
  human review path.

## Acceptance criteria
1. Live run: flagship canonical → managers profile → touch_list is a
   minority of blocks with sane reasons; untouched blocks byte-
   identical; all checks green; auto-published; evidence populated.
   Print touch rate + dispositions summary.
2. Sabotage tests (pytest, no API): fixture version with (a) mutated
   correct-flag, (b) edited locked block, (c) +60% length block —
   each caught by the named deterministic check; version halts.
3. Disposition unit test with stubbed model responses: gratuitous
   synonym-churn case → revert_to_canonical.
4. Full pytest suite green; py_compile clean.
5. Do not push; summarize diff, touch rate, and any judgment calls.
Ask before any run that spends API tokens.
