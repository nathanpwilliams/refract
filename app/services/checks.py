"""Deterministic check suite — code, not AI (pipeline v2, §2 of the brief).

These six checks are the warranty's deterministic floor: for what they
cover they have perfect recall, because they are computed, not judged.
Any failure halts the version (see app/services/pipeline.py, §6).

    verbatim_integrity    untouched blocks identical to canonical
    locked_integrity      adaptable=false blocks identical to canonical
    answer_key_integrity  option ids/count/correct flags/quality ratings
    length_budget         touched prose within +/-30% of canonical words
    structure_integrity   block_type, content keys, block sequence
    vocabulary_ban        profile avoid-list terms absent from rewrites

Pure functions over two block lists: no database, no model, no I/O. That
is what makes them testable by sabotage fixtures (tests/test_checks.py)
and what makes their verdict reproducible from the evidence file.

"Identical" means identical in CANONICAL JSON FORM (sorted keys, tight
separators), not byte-identical in storage: content round-trips through
jsonb, which does not preserve key order or whitespace. Canonical form is
the strongest equality that survives the storage layer.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field

# Keys whose values are identifiers/config rather than prose. Excluded from
# word counts and from the vocabulary ban so a storage path or an option id
# can never trip a prose check.
NON_PROSE_KEYS = frozenset({
    "storage_path", "url", "quiz_id", "id", "language", "variant", "style",
    "quality", "correct", "level", "captions", "block_id", "objective_ref",
})

# length_budget bound. The +/-30% is the brief's hard bound; the absolute
# floor keeps it meaningful on very short blocks — at +/-30% a 3-word
# heading would be allowed exactly 3 words, making any legitimate
# adaptation of a short line a halt. See docs/pipeline-v2.md.
LENGTH_TOLERANCE = 0.30
LENGTH_FLOOR_WORDS = 2


@dataclass(frozen=True)
class BlockView:
    """One block, from either side of the comparison.

    block_id is always the CANONICAL block's id: adapted copies carry it
    as source_block_id, which is the lineage key the whole pipeline pairs
    on.
    """
    block_id: str
    position: int
    block_type: str
    adaptable: bool
    content: dict


@dataclass(frozen=True)
class CheckResult:
    check: str
    block_id: str | None      # None => a version-level result
    passed: bool
    detail: str

    def to_dict(self) -> dict:
        return {
            "check": self.check,
            "block_id": self.block_id,
            "pass": self.passed,
            "detail": self.detail,
        }


@dataclass
class CheckOutcome:
    """Everything run_checks learned, in machine-readable form."""
    results: list[CheckResult] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]

    @property
    def passed(self) -> bool:
        return not self.failures

    def summary(self) -> dict:
        by_check: dict[str, dict[str, int]] = {}
        for r in self.results:
            tally = by_check.setdefault(r.check, {"pass": 0, "fail": 0})
            tally["pass" if r.passed else "fail"] += 1
        return {
            "total": len(self.results),
            "failed": len(self.failures),
            "by_check": by_check,
            "failures": [r.to_dict() for r in self.failures],
        }


# ------------------------------------------------------------------ helpers

def canonical_form(content: dict) -> str:
    """Stable serialization for equality comparison across a jsonb trip."""
    return json.dumps(content, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def prose_strings(content, key: str | None = None) -> list[str]:
    """Every prose string in a content tree, skipping identifier fields."""
    if key in NON_PROSE_KEYS:
        return []
    if isinstance(content, str):
        return [content]
    if isinstance(content, dict):
        out: list[str] = []
        for k, v in content.items():
            out.extend(prose_strings(v, k))
        return out
    if isinstance(content, list):
        out = []
        for v in content:
            out.extend(prose_strings(v, key))
        return out
    return []


def word_count(content: dict) -> int:
    return sum(len(s.split()) for s in prose_strings(content))


def canonical_digest(blocks: list[BlockView]) -> str:
    """sha256 over the ordered canonical block contents.

    Binds an evidence file to the exact canonical text the run was derived
    from: re-ingesting the authoring files changes this digest, so an
    evidence file can never silently describe different source content.
    Callers must pass blocks in course order (module, lesson, position).
    """
    joined = "\n".join(canonical_form(b.content) for b in blocks)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def avoid_terms(profile: dict) -> list[str]:
    """Avoid-list terms from a profile, with qualifiers stripped.

    Profiles write human qualifiers into the list — "leverage (as a verb)",
    "sprint (unless agile context)". The parenthetical is guidance for the
    rewriter, not part of the term; left in, it would match nothing.
    """
    vocab = profile.get("vocabulary") or {}
    raw = vocab.get("avoid") or vocab.get("banned") or []
    terms = []
    for item in raw:
        if not isinstance(item, str):
            continue
        term = re.sub(r"\([^)]*\)", " ", item).strip(" \t-–—,;")
        if term:
            terms.append(term)
    return terms


def _term_pattern(term: str) -> re.Pattern:
    inner = r"\s+".join(re.escape(w) for w in term.split())
    return re.compile(rf"(?<!\w){inner}(?!\w)", re.IGNORECASE)


def _answer_key(content: dict) -> list[tuple] | None:
    """Normalized answer key, or None if this block has no answer key.

    knowledge_check / quiz questions: (option id, correct flag).
    scenario: (choice id, quality rating).
    """
    if isinstance(content.get("options"), list):
        return [(o.get("id"), bool(o.get("correct")))
                for o in content["options"] if isinstance(o, dict)]
    if isinstance(content.get("choices"), list):
        return [(c.get("id"), c.get("quality"))
                for c in content["choices"] if isinstance(c, dict)]
    return None


# ------------------------------------------------------------------ the suite

def run_checks(
    *,
    canonical: list[BlockView],
    adapted: list[BlockView],
    touched_ids: set[str],
    profile: dict,
    plan_touched_locked: set[str] = frozenset(),
) -> CheckOutcome:
    """Run every check over the new version against the canonical.

    canonical/adapted are both in course order; adapted[i].block_id is the
    canonical block it descends from. plan_touched_locked holds canonical
    ids that PLAN selected despite adaptable=false — a planning error that
    hard-fails locked_integrity even though the pipeline refused to send
    those blocks to REWRITE.
    """
    out = CheckOutcome()
    by_id = {b.block_id: b for b in adapted}

    out.results.append(_sequence_result(canonical, adapted))

    for block in canonical:
        new = by_id.get(block.block_id)
        if new is None:
            # Missing entirely: the sequence check already failed; record it
            # per-block too so the diagnosis names the block.
            out.results.append(CheckResult(
                "structure_integrity", block.block_id, False,
                f"block at position {block.position} is absent from the adapted version"))
            continue

        touched = block.block_id in touched_ids
        identical = canonical_form(block.content) == canonical_form(new.content)

        out.results.extend(_locked_integrity(block, identical, plan_touched_locked))
        if not touched:
            out.results.append(_verbatim_integrity(block, identical))
        out.results.append(_structure_integrity(block, new))
        key_result = _answer_key_integrity(block, new)
        if key_result is not None:
            out.results.append(key_result)
        if touched:
            budget = _length_budget(block, new)
            if budget is not None:
                out.results.append(budget)
            out.results.extend(_vocabulary_ban(new, profile))

    return out


def _sequence_result(canonical: list[BlockView],
                     adapted: list[BlockView]) -> CheckResult:
    """No blocks added, removed, or reordered."""
    want = [(b.position, b.block_id) for b in canonical]
    got = [(b.position, b.block_id) for b in adapted]
    if want == got:
        return CheckResult("structure_integrity", None, True,
                           f"{len(want)} blocks, same order as canonical")
    detail = (f"block sequence differs: canonical has {len(want)} blocks, "
              f"adapted has {len(got)}")
    missing = [b for p, b in want if (p, b) not in got]
    added = [b for p, b in got if (p, b) not in want]
    if missing:
        detail += f"; missing/moved canonical ids {missing[:5]}"
    if added:
        detail += f"; unexpected ids {added[:5]}"
    return CheckResult("structure_integrity", None, False, detail)


def _locked_integrity(block: BlockView, identical: bool,
                      plan_touched_locked: set[str]) -> list[CheckResult]:
    if block.adaptable:
        return []
    if block.block_id in plan_touched_locked:
        return [CheckResult(
            "locked_integrity", block.block_id, False,
            f"PLAN selected locked block at position {block.position} "
            "(adaptable=false); the block was not sent to REWRITE, but a plan "
            "that targets locked content is a hard fail")]
    if not identical:
        return [CheckResult(
            "locked_integrity", block.block_id, False,
            f"locked block at position {block.position} (adaptable=false) "
            "differs from canonical")]
    return [CheckResult("locked_integrity", block.block_id, True,
                        "locked block identical to canonical")]


def _verbatim_integrity(block: BlockView, identical: bool) -> CheckResult:
    if identical:
        return CheckResult("verbatim_integrity", block.block_id, True,
                           "untouched block identical to canonical")
    return CheckResult(
        "verbatim_integrity", block.block_id, False,
        f"untouched block at position {block.position} differs from canonical "
        "— untouched blocks must be copied, never generated")


def _structure_integrity(block: BlockView, new: BlockView) -> CheckResult:
    if block.block_type != new.block_type:
        return CheckResult(
            "structure_integrity", block.block_id, False,
            f"block_type changed {block.block_type!r} -> {new.block_type!r}")
    missing = sorted(set(block.content) - set(new.content))
    if missing:
        return CheckResult(
            "structure_integrity", block.block_id, False,
            f"adapted content is missing canonical keys {missing}")
    return CheckResult("structure_integrity", block.block_id, True,
                       f"{block.block_type}; content keys superset-compatible")


def _answer_key_integrity(block: BlockView, new: BlockView) -> CheckResult | None:
    want = _answer_key(block.content)
    if want is None:
        return None
    got = _answer_key(new.content)
    if got is None:
        return CheckResult(
            "answer_key_integrity", block.block_id, False,
            "adapted block lost its options/choices structure entirely")
    if len(want) != len(got):
        return CheckResult(
            "answer_key_integrity", block.block_id, False,
            f"option count changed {len(want)} -> {len(got)}")
    diffs = [f"{w[0]!r}: {w[1]!r} -> {g[1]!r}" if w[0] == g[0]
             else f"id {w[0]!r} -> {g[0]!r}"
             for w, g in zip(want, got) if w != g]
    if diffs:
        return CheckResult(
            "answer_key_integrity", block.block_id, False,
            "answer key changed — " + "; ".join(diffs))
    return CheckResult("answer_key_integrity", block.block_id, True,
                       f"{len(want)} options, ids and keys unchanged")


def _length_budget(block: BlockView, new: BlockView) -> CheckResult | None:
    before = word_count(block.content)
    if before == 0:
        return None                     # nothing text-bearing to bound
    after = word_count(new.content)
    low = min(math.ceil(before * (1 - LENGTH_TOLERANCE)), before - LENGTH_FLOOR_WORDS)
    high = max(math.floor(before * (1 + LENGTH_TOLERANCE)), before + LENGTH_FLOOR_WORDS)
    if low <= after <= high:
        return CheckResult("length_budget", block.block_id, True,
                           f"{before} -> {after} words (allowed {low}-{high})")
    delta = (after - before) / before
    return CheckResult(
        "length_budget", block.block_id, False,
        f"{before} -> {after} words ({delta:+.0%}), outside allowed {low}-{high}")


def _vocabulary_ban(new: BlockView, profile: dict) -> list[CheckResult]:
    """Avoid-list terms must not appear in text the pipeline rewrote.

    Scoped to TOUCHED blocks on purpose. An untouched block is canonical
    text copied verbatim; if the canonical legitimately uses a term this
    audience avoids, that is a canonical/profile conflict for an author to
    resolve — and one no reversion could fix, since reverting a copied
    block is a no-op. What this check guards is the transformation: the
    pipeline must not INTRODUCE banned vocabulary.
    """
    terms = avoid_terms(profile)
    if not terms:
        return []
    text = " ".join(prose_strings(new.content))
    hits = [t for t in terms if _term_pattern(t).search(text)]
    if hits:
        return [CheckResult(
            "vocabulary_ban", new.block_id, False,
            "rewritten text contains avoid-list terms: "
            + ", ".join(repr(h) for h in hits))]
    return [CheckResult("vocabulary_ban", new.block_id, True,
                        f"none of {len(terms)} avoid-list terms present")]
