# Pipeline v2 — sparse refraction + warranty chain

Implements `docs/pipeline-v2-brief.md`. This document is the operator's
and auditor's view: what the machine actually guarantees, what it does
not, and how to read a halt.

## The claim

**The canonical is human-warranted once; every refraction is a
machine-proven bounded transformation of it.**

v1 asked a human to review every AI-adapted block. Across five
production runs that degenerated into rubber-stamping — the review was
real on paper and nominal in practice — while uniform rewriting produced
synonym churn on blocks no audience profile had any reason to change.
v2 removes the human from the run and replaces that signature with four
guarantees of descending strength:

| # | Guarantee | Strength |
|---|-----------|----------|
| 1 | **Sparsity** — untouched blocks have NO model contact; they are copied by `insert … select` from the canonical row | structural. Most of a version is canonical by construction, not by inspection |
| 2 | **Deterministic checks** (`app/services/checks.py`) — computed, not judged | perfect recall *over what they cover*. Any failure halts |
| 3 | **Cross-model verification** (`app/services/verifiers.py`) — VERIFY twice on two models; a blocker from either halts | partial. Two Claude models are only partially decorrelated (see below) |
| 4 | **Disposition** (`app/services/disposition.py`) — AI review whose null preference is the canonical | judgment, with a safe default |

Stages 1 and 2 are the warranty. Stages 3 and 4 raise the cost of a miss;
they do not make one impossible.

## The flow

```
PLAN ──> touch_list ──> REWRITE (touched blocks only) ──> CHECKS
     ──> VERIFY ×2 (two models) ──> DISPOSITION ──> publish | halt
```

Job statuses: `planning → rewriting → checking → verifying → disposing →
done | halted`. There is no `awaiting_review` in this flow (the status
survives in the constraint for the future `client_sme` tier).

- **PLAN** sees the whole course — every block's type, `adaptable` flag,
  word count, and a 240-character prose preview — plus the audience
  profile, and returns a per-lesson `touch_list` of
  `{block_position, reason, adaptation_instruction}`. It is written as a
  gatekeeper, not an editor: the rule is concrete audience cause or no
  touch. The touch rate is logged and recorded in evidence; above 50% the
  run logs a planning smell (not a halt).
- **REWRITE** receives only touched blocks, each with its own
  instruction, its canonical word count, and the computed
  `allowed_word_count` range from `checks.length_bounds()` — the same
  function the check judges by, so the instruction and the enforcement
  cannot drift. It is told explicitly that returning a block unchanged is
  an acceptable answer, and that assessments may change their setting but
  never the decision being tested.
- **CHECKS** run over the target version **read back from the database**,
  against the canonical. The warranty is a claim about what is stored,
  not about what the process believed it wrote.
- **VERIFY** runs twice at temperature 0 on `VERIFY_MODEL_A` and
  `VERIFY_MODEL_B`; findings are merged with a `source` tag and never
  de-duplicated (agreement is evidence). It is told not to judge length:
  `length_budget` computes that with perfect recall, and in a live run
  both verifiers raised blocker-severity length findings on a block the
  computed check had passed — one of them saying mid-sentence that the
  block was within tolerance.
- **DISPOSITION** decides each touched block: `accept`,
  `revert_to_canonical`, or `halt`.

## The deterministic suite

| Check | Scope | Fails when | On failure |
|-------|-------|-----------|-----------|
| `verbatim_integrity` | untouched blocks | content differs from canonical at all | **halt** |
| `locked_integrity` | `adaptable=false` blocks | content differs, **or** PLAN selected the block | **halt** |
| `structure_integrity` | every block + the version | `block_type` changed, a canonical content key dropped, or blocks added/removed/reordered | **halt** |
| `revert_integrity` | reverted blocks | a reverted block is not byte-identical to canonical | **halt** |
| `answer_key_integrity` | `knowledge_check`, `scenario`, question shapes | option ids, option count, `correct` flags, or `quality` ratings changed | revert the block |
| `length_budget` | touched, text-bearing | word count outside ±30% (with a ±2-word floor) | revert the block |
| `vocabulary_ban` | touched blocks | the profile's avoid-list terms appear in rewritten text | revert the block |

`checks.REVERTIBLE_CHECKS` and `checks.HALTING_CHECKS` partition
`ALL_CHECKS`, and a test enforces the partition — a new check cannot be
added without deciding which side it belongs on. A revertible failure on
a block nobody touched still halts: the rule is "one touched block's
adapted prose", and anything else is a claim about the version.

Three details worth knowing before you read a halt:

- **"Identical" means identical in canonical JSON form** (sorted keys,
  tight separators), not byte-identical in storage. `jsonb` preserves
  neither key order nor whitespace, so canonical form is the strongest
  equality that survives the storage layer.
- **`length_budget` has a ±2-word absolute floor.** At a strict ±30% a
  three-word heading is allowed exactly three words, which would make any
  legitimate adaptation of a short line a halt. Tune
  `checks.LENGTH_FLOOR_WORDS` if you want the brief's bound verbatim.
- **`vocabulary_ban` only looks at touched blocks.** An untouched block
  is canonical text copied verbatim; a banned term there is a
  canonical/profile conflict for an author to resolve, and one no
  reversion could fix. What this check guards is the transformation: the
  pipeline must not *introduce* banned vocabulary.
- **A locked block PLAN selected is never sent to REWRITE.** v1's
  invariant — `adaptable=false` content never reaches a model — is
  preserved, and the planning error itself hard-fails `locked_integrity`.
  A regulation quotation should not be fed to a rewriter and then caught.

## Decorrelation caveat (read this before quoting the warranty)

`VERIFY_MODEL_A` and `VERIFY_MODEL_B` default to two Claude models.
**Same-vendor models are only partially decorrelated**: they share
training lineage, data, and post-training, so a blind spot in one can
survive in the other. Two passes catch independent sloppiness — a missed
fact drift, an unflagged tone problem — but they are weak evidence
against a *systematic* error both models would make.

A second-vendor verifier is the roadmap hardening. The interface is
provider-agnostic for exactly that reason: `VerifierSpec` carries a
provider, `verifiers._PROVIDERS` is a registry, and the env values accept
a `<provider>:<model>` prefix. Adding an OpenAI or Gemini verifier is one
class with a `verify()` method, one registry entry, and an env change —
no pipeline change.

Until then, the honest statement of what a published version carries is:
deterministic conformance to the canonical over six properties, plus two
partially-independent model reviews, plus a reversion-biased AI
disposition. Not: "two independent models agreed."

## Publish and halt — per-block fallback

Defects are handled per block, not per run. **A touched block that fails
any deterministic check, or draws a `blocker` from either verifier, is
reverted to canonical in code** — the disposition stage is never asked
about it, because the checks and the blocker rule have already decided
and the canonical text is the answer. The version then publishes with the
adaptations that survive (`status='published'`, `published_at=now()`, job
`done`).

Reverting is proved, not assumed: the version is read back from storage
and `revert_integrity` requires every reverted block to be byte-identical
to its canonical.

**The run halts only for what a reversion cannot cure:**

| Halt condition | Why reversion doesn't help |
|---|---|
| an untouched or locked block failed integrity | the defect is that a block which should have been copied wasn't — there is no adaptation to withdraw |
| `structure_integrity` failed | the claim is about the version's shape, not one block's prose |
| a reverted block is not byte-identical to canonical | the reversion itself did not take |
| zero adaptations survive | nothing was refracted; a byte-identical copy is not an audience version |
| a halt disposition | §4 defines that decision as "something reversion doesn't fix" |

Two consequences worth knowing. A blocker finding on a block id that was
never touched is **recorded in evidence and not acted on** — the
verifiers only ever see touched pairs, so an unmatched id describes
nothing in the version, and halting on a model's typo would be a worse
failure mode than ignoring it. And an empty touch list now halts rather
than publishing a verbatim copy, under the zero-survivors rule.

This is a deliberate revision of the brief's original §6 (all-or-nothing)
after three live runs, each of which halted over one or two defective
blocks while eight or nine good adaptations were discarded with them. The
warranty is unchanged in what it permits to ship; what changed is the
blast radius of a single bad block.

**A halted version is a diagnostic artifact, not a review queue.** You
fix it by changing the canonical, the profile, or the prompts and
re-running — never by editing the halted version. Two guards enforce
this:

- `POST /adaptation/versions/{id}/publish` returns 409 for a halted
  version.
- `POST /adaptation/revisions/{id}/review` returns 409 for any revision
  whose `reviewer` is `pipeline_v2`.

The human review API and the `/review` UI are otherwise untouched and
still work; they are kept for the future `client_sme` tier. v2 never
writes `review_status='pending'`, so the publish endpoint's existing 409
pending-review gate is satisfied by construction rather than weakened.

## Evidence

`adaptation_jobs.evidence` (migration 0005) is the compliance file's data
layer — PDF rendering is a later task. Read it with
`GET /adaptation/jobs/{job_id}/evidence`, which also returns the frozen
`profile_snapshot` and the plan. It records:

- `pipeline_version`, `generated_at`
- `models` — plan / rewrite / disposition / verify_a / verify_b
- `prompt_versions` — from `prompts.PROMPT_VERSIONS`; **bump the version
  string whenever a prompt's text changes**, or an evidence file will
  name instructions that no longer exist
- `touch_rate` — touched, total, rate, and per-lesson counts
- `adaptations` — `planned`, `surviving`, `reverted`, and
  `reverted_blocks`: every reverted block with its cause and whether the
  fallback or the disposition stage reverted it
- `unattributed_blockers` — blocker findings naming a block that was
  never touched; recorded, not acted on
- `plan_warnings` — positions PLAN got wrong, locked blocks it selected
- `checks` — the full tally plus every failure in full
- `findings` — totals by source, and every blocker in full
- `dispositions` — accepted / reverted / halted / unchanged, with halt reasons
- `canonical` — version id, block count, and a **sha256 over the ordered
  canonical block contents**. Re-ingesting the authoring files changes
  this digest, so an evidence file can never silently describe different
  source content.

`profile_snapshot` is written at the start of the run because profiles are
versioned content authors keep editing; evidence pointing at a live
profile row would describe the wrong inputs the moment someone revised it.

## Configuration

```
ADAPTATION_MODEL=claude-sonnet-4-6     # PLAN / REWRITE / DISPOSITION
VERIFY_MODEL_A=                        # empty => ADAPTATION_MODEL
VERIFY_MODEL_B=claude-haiku-4-5        # "<provider>:<model>" also accepted
```

`PLAN_TEMPERATURE` (0.0), `REWRITE_TEMPERATURE` (0.3),
`VERIFY_TEMPERATURE` (0.0), and `DISPOSITION_TEMPERATURE` (0.0) are also
settable. `.env` changes require restarting uvicorn — settings are cached.

Note that several current Claude models removed the sampling parameters
and reject `temperature` with a 400. `llm.supports_temperature()` drops
it for those models rather than failing, so any model can be configured
as a verifier.

## Running one

```bash
source .venv/bin/activate
python scripts/run_refraction.py --dry-run    # resolve inputs, spend nothing
python scripts/run_refraction.py --yes        # SPENDS API TOKENS
```

The script creates the target version and job exactly as the API does,
runs the pipeline in-process, and prints the touch rate, the check tally,
the merged findings, the dispositions, and the outcome. Cost per run is
1 PLAN call + (1 REWRITE + 2 VERIFY + 1 DISPOSITION) per lesson that has
touched blocks.

## Tests

No test in the suite calls an API or touches a database.

- `tests/test_checks.py` — sabotage fixtures: mutated correct flag,
  promoted `quality` rating, dropped option, edited locked block,
  PLAN-selected locked block, +60% length, changed untouched block,
  `block_type` change, dropped content key, removed and reordered blocks,
  avoid-list hit. Each asserts the *named* check catches it.
- `tests/test_disposition.py` — stubbed model responses; the synonym-churn
  case reverts, and every malformed response shape reverts rather than
  accepts.
- `tests/test_verifiers.py` — spec parsing, source tagging, and the
  either-verifier halt rule.
- `tests/test_pipeline_plan.py` — touch-list resolution, what PLAN is
  shown, and halt-cause assembly.
