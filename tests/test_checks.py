"""Sabotage tests for the deterministic check suite (no API, no database).

Each test takes a known-good adapted version, breaks exactly one thing,
and asserts that the NAMED check catches it and the version would halt.
These are the warranty's regression tests: if one of them ever goes green
on sabotage, the floor has a hole in it.
"""
import copy

import pytest

from app.services import checks
from app.services.checks import BlockView

PROFILE = {
    "name": "managers",
    "vocabulary": {
        "preferred": ["direct report", "sign-off"],
        "avoid": ["synergy", "circle back", "leverage (as a verb)"],
    },
}

CANONICAL_CONTENT = {
    1: {"level": 2, "text": "Why this matters"},
    2: {"markdown": (
        "A language model predicts the next token from patterns in its "
        "training data. It does not look up facts in a database, which is "
        "why it can be fluent and wrong at the same time. Treat every "
        "output as a draft from a fast, confident stranger.")},
    3: {"markdown": (
        "Hallucination is not a bug that will be patched out. It is the "
        "same mechanism that produces useful text, running without a "
        "source to check against.")},
    4: {"question": "A summary cites a figure you do not recognize. What do you do?",
        "options": [
            {"id": "a", "text": "Check the source document",
             "correct": True, "feedback": "Right — verify before you rely."},
            {"id": "b", "text": "Send it on; the model is usually right",
             "correct": False, "feedback": "Confidence is not accuracy."},
            {"id": "c", "text": "Ask the model if it is sure",
             "correct": False, "feedback": "It cannot audit itself."},
        ],
        "style": "single"},
    5: {"situation_md": (
        "A deadline is in two hours and you need a summary of a long "
        "report for a client."),
        "choices": [
            {"id": "a", "text": "Paste the report and ask against the text",
             "consequence_md": "Traceable, and fast enough.", "quality": "best"},
            {"id": "b", "text": "Ask from memory of the topic",
             "consequence_md": "Plausible text, unsourced claims.", "quality": "poor"},
        ]},
}

LOCKED_POSITION = 3


def _canonical() -> list[BlockView]:
    types = {1: "heading", 2: "text", 3: "text", 4: "knowledge_check",
             5: "scenario"}
    return [
        BlockView(
            block_id=f"block-{pos}",
            position=pos,
            block_type=types[pos],
            adaptable=pos != LOCKED_POSITION,
            content=copy.deepcopy(content),
        )
        for pos, content in sorted(CANONICAL_CONTENT.items())
    ]


# A clean refraction: positions 2, 4 and 5 touched and genuinely adapted,
# 1 and 3 copied through untouched.
ADAPTED_OVERRIDES = {
    2: {"markdown": (
        "A language model predicts the next token from patterns in its "
        "training data. It does not look up facts in a database, which is "
        "why a status summary can read as confident and still be wrong. "
        "Treat every draft as work from a fast, confident stranger.")},
    4: {"question": ("A direct report's status summary cites a variance "
                     "figure you do not recognize. What do you do?"),
        "options": [
            {"id": "a", "text": "Check it against the budget report",
             "correct": True, "feedback": "Right — verify before you sign off."},
            {"id": "b", "text": "Pass it upward; the model is usually right",
             "correct": False, "feedback": "Confidence is not accuracy, and the "
                                           "sign-off is yours."},
            {"id": "c", "text": "Ask the model if it is sure",
             "correct": False, "feedback": "It cannot audit itself."},
        ],
        "style": "single"},
    5: {"situation_md": (
        "A director wants the month's status reports summarized in two "
        "hours and you have a stack of them."),
        "choices": [
            {"id": "a", "text": "Paste the reports and ask against the text",
             "consequence_md": "Traceable, and fast enough to make the deadline.",
             "quality": "best"},
            {"id": "b", "text": "Summarize from what you remember of the month",
             "consequence_md": "Plausible text, commitments you cannot source.",
             "quality": "poor"},
        ]},
}

TOUCHED_IDS = {"block-2", "block-4", "block-5"}


def _adapted() -> list[BlockView]:
    out = []
    for b in _canonical():
        override = ADAPTED_OVERRIDES.get(b.position)
        out.append(BlockView(
            block_id=b.block_id, position=b.position, block_type=b.block_type,
            adaptable=b.adaptable,
            content=copy.deepcopy(override) if override else copy.deepcopy(b.content),
        ))
    return out


def _run(adapted, *, touched=None, profile=PROFILE, plan_touched_locked=frozenset()):
    return checks.run_checks(
        canonical=_canonical(),
        adapted=adapted,
        touched_ids=set(TOUCHED_IDS if touched is None else touched),
        profile=profile,
        plan_touched_locked=plan_touched_locked,
    )


def _failed_checks(outcome) -> set[str]:
    return {r.check for r in outcome.failures}


def _block(blocks, position) -> BlockView:
    return next(b for b in blocks if b.position == position)


def _replace(blocks, position, content) -> list[BlockView]:
    out = []
    for b in blocks:
        out.append(BlockView(b.block_id, b.position, b.block_type, b.adaptable,
                             content) if b.position == position else b)
    return out


# ---------------------------------------------------------------- happy path

def test_clean_refraction_passes_every_check():
    outcome = _run(_adapted())
    assert outcome.passed, [r.to_dict() for r in outcome.failures]
    assert outcome.summary()["failed"] == 0
    # every check in the suite actually ran against this fixture
    assert set(outcome.summary()["by_check"]) == {
        "verbatim_integrity", "locked_integrity", "answer_key_integrity",
        "length_budget", "structure_integrity", "vocabulary_ban",
    }


# ------------------------------------------------- sabotage (a): answer key

def test_mutated_correct_flag_is_caught_by_answer_key_integrity():
    adapted = _adapted()
    content = copy.deepcopy(_block(adapted, 4).content)
    content["options"][0]["correct"] = False
    content["options"][1]["correct"] = True          # answer key moved a -> b
    outcome = _run(_replace(adapted, 4, content))

    assert not outcome.passed
    assert _failed_checks(outcome) == {"answer_key_integrity"}
    failure = outcome.failures[0]
    assert failure.block_id == "block-4"
    assert "answer key changed" in failure.detail


def test_mutated_scenario_quality_rating_is_caught():
    adapted = _adapted()
    content = copy.deepcopy(_block(adapted, 5).content)
    content["choices"][1]["quality"] = "acceptable"   # 'poor' promoted
    outcome = _run(_replace(adapted, 5, content))

    assert _failed_checks(outcome) == {"answer_key_integrity"}
    assert "'poor' -> 'acceptable'" in outcome.failures[0].detail


def test_dropped_option_is_caught_as_count_change():
    adapted = _adapted()
    content = copy.deepcopy(_block(adapted, 4).content)
    content["options"].pop()
    outcome = _run(_replace(adapted, 4, content))

    assert "answer_key_integrity" in _failed_checks(outcome)
    assert "option count changed 3 -> 2" in outcome.failures[0].detail


# ---------------------------------------------- sabotage (b): locked block

def test_edited_locked_block_is_caught_by_locked_integrity():
    adapted = _adapted()
    content = {"markdown": (
        "Hallucination is not a bug that will be patched out — it is the "
        "same mechanism that writes useful status updates, running without "
        "a source to check against.")}
    # Untouched by PLAN, so this also trips verbatim_integrity: a locked
    # block that changed is both a locked-content breach and a block that
    # was generated when it should have been copied.
    outcome = _run(_replace(adapted, LOCKED_POSITION, content))

    assert not outcome.passed
    assert "locked_integrity" in _failed_checks(outcome)
    locked = next(r for r in outcome.failures if r.check == "locked_integrity")
    assert locked.block_id == f"block-{LOCKED_POSITION}"
    assert "adaptable=false" in locked.detail


def test_plan_selecting_a_locked_block_hard_fails_even_if_unchanged():
    # The pipeline refuses to send locked content to REWRITE, so the block
    # is byte-identical; the PLANNING error must still halt the run.
    outcome = _run(_adapted(),
                   plan_touched_locked={f"block-{LOCKED_POSITION}"})

    assert not outcome.passed
    assert _failed_checks(outcome) == {"locked_integrity"}
    assert "PLAN selected locked block" in outcome.failures[0].detail


# ------------------------------------------------- sabotage (c): length

def test_sixty_percent_longer_block_is_caught_by_length_budget():
    adapted = _adapted()
    original_words = checks.word_count(_block(_canonical(), 2).content)
    base = _block(adapted, 2).content["markdown"]
    padding = " ".join(["Managers carry the sign-off for this."] * 8)
    content = {"markdown": base + " " + padding}
    assert checks.word_count(content) > original_words * 1.6

    outcome = _run(_replace(adapted, 2, content))

    assert not outcome.passed
    assert _failed_checks(outcome) == {"length_budget"}
    failure = outcome.failures[0]
    assert failure.block_id == "block-2"
    assert "outside allowed" in failure.detail


def test_short_block_gets_the_absolute_word_floor():
    # A 3-word heading at +/-30% would be allowed exactly 3 words; the floor
    # keeps the bound from halting on any legitimate short rewrite.
    canonical = [BlockView("h", 1, "heading", True, {"level": 2, "text": "Why this matters"})]
    adapted = [BlockView("h", 1, "heading", True,
                         {"level": 2, "text": "Why this matters to managers"})]
    outcome = checks.run_checks(canonical=canonical, adapted=adapted,
                                touched_ids={"h"}, profile=PROFILE)
    assert outcome.passed, [r.to_dict() for r in outcome.failures]


# ------------------------------------------------------ other checks

def test_changed_untouched_block_is_caught_by_verbatim_integrity():
    adapted = _adapted()
    content = {"markdown": _block(adapted, 2).content["markdown"]}
    outcome = _run(_replace(adapted, 2, content), touched=set())

    assert _failed_checks(outcome) == {"verbatim_integrity"}
    assert "untouched blocks must be copied" in outcome.failures[0].detail


def test_block_type_change_is_caught_by_structure_integrity():
    adapted = [
        BlockView(b.block_id, b.position, "callout" if b.position == 2 else b.block_type,
                  b.adaptable, b.content)
        for b in _adapted()
    ]
    outcome = _run(adapted)
    assert "structure_integrity" in _failed_checks(outcome)
    assert "block_type changed" in outcome.failures[0].detail


def test_dropped_content_key_is_caught_by_structure_integrity():
    adapted = _adapted()
    content = {"question": _block(adapted, 4).content["question"],
               "options": _block(adapted, 4).content["options"]}   # 'style' gone
    outcome = _run(_replace(adapted, 4, content))

    assert "structure_integrity" in _failed_checks(outcome)
    assert "missing canonical keys ['style']" in outcome.failures[0].detail


def test_removed_block_is_caught_by_structure_integrity():
    adapted = [b for b in _adapted() if b.position != 5]
    outcome = _run(adapted)

    failures = [r for r in outcome.failures if r.check == "structure_integrity"]
    assert any(r.block_id is None for r in failures)        # version-level
    assert any(r.block_id == "block-5" for r in failures)   # named block


def test_reordered_blocks_are_caught_by_structure_integrity():
    adapted = _adapted()
    swapped = adapted[:1] + [adapted[2], adapted[1]] + adapted[3:]
    # same ids, same positions, different order in the sequence
    outcome = checks.run_checks(canonical=_canonical(), adapted=swapped,
                                touched_ids=set(TOUCHED_IDS), profile=PROFILE)
    assert any(r.check == "structure_integrity" and r.block_id is None
               for r in outcome.failures)


def test_avoid_list_term_in_a_rewrite_is_caught_by_vocabulary_ban():
    adapted = _adapted()
    content = {"markdown": _block(adapted, 2).content["markdown"].replace(
        "Treat every draft as work from", "Circle back and treat every draft as")}
    outcome = _run(_replace(adapted, 2, content))

    assert _failed_checks(outcome) == {"vocabulary_ban"}
    assert "'circle back'" in outcome.failures[0].detail


def test_avoid_list_qualifiers_are_stripped_before_matching():
    assert checks.avoid_terms(PROFILE) == ["synergy", "circle back", "leverage"]


def test_vocabulary_ban_matches_on_word_boundaries_only():
    adapted = _adapted()
    # "synergy" is banned; "synergistically" is a different token and the
    # check must not fire on a substring hit inside an unrelated word.
    content = {"markdown": _block(adapted, 2).content["markdown"].replace(
        "a status summary", "a Synergy Partners status summary")}
    assert "vocabulary_ban" in _failed_checks(_run(_replace(adapted, 2, content)))

    clean = {"markdown": _block(adapted, 2).content["markdown"].replace(
        "a status summary", "a synergyless status summary")}
    assert "vocabulary_ban" not in _failed_checks(_run(_replace(adapted, 2, clean)))


def test_vocabulary_ban_ignores_untouched_blocks():
    # Position 3 is untouched canonical text; even a banned term there is a
    # canonical/profile conflict, not a transformation defect.
    canonical = _canonical()
    content = {"markdown": "Do not circle back on hallucination."}
    canonical = [
        BlockView(b.block_id, b.position, b.block_type, b.adaptable,
                  content if b.position == 2 else b.content)
        for b in canonical
    ]
    adapted = [BlockView(b.block_id, b.position, b.block_type, b.adaptable,
                         b.content) for b in canonical]
    outcome = checks.run_checks(canonical=canonical, adapted=adapted,
                                touched_ids=set(), profile=PROFILE)
    assert outcome.passed, [r.to_dict() for r in outcome.failures]


def test_profile_without_vocabulary_skips_the_ban():
    outcome = _run(_adapted(), profile={"name": "managers"})
    assert outcome.passed
    assert "vocabulary_ban" not in outcome.summary()["by_check"]


# ------------------------------------------------------ supporting machinery

def test_canonical_form_ignores_key_order_but_not_values():
    assert (checks.canonical_form({"a": 1, "b": 2})
            == checks.canonical_form({"b": 2, "a": 1}))
    assert (checks.canonical_form({"a": 1})
            != checks.canonical_form({"a": "1"}))


def test_word_count_skips_identifier_fields():
    content = {"storage_path": "flagship/1-1-image-1.png",
               "alt_text": "a two by two grid",
               "caption": "Figure 1"}
    assert checks.word_count(content) == len("a two by two grid".split()) + 2


def test_canonical_digest_is_order_sensitive_and_content_sensitive():
    blocks = _canonical()
    baseline = checks.canonical_digest(blocks)

    assert checks.canonical_digest(_canonical()) == baseline      # reproducible
    assert checks.canonical_digest(list(reversed(blocks))) != baseline
    mutated = _replace(blocks, 2, {"markdown": "different canonical text"})
    assert checks.canonical_digest(mutated) != baseline


@pytest.mark.parametrize("before,after,ok", [
    (100, 100, True), (100, 130, True), (100, 131, False),
    (100, 70, True), (100, 69, False), (10, 12, True), (10, 20, False),
])
def test_length_budget_bounds(before, after, ok):
    canonical = [BlockView("b", 1, "text", True,
                           {"markdown": " ".join(["word"] * before)})]
    adapted = [BlockView("b", 1, "text", True,
                         {"markdown": " ".join(["word"] * after)})]
    outcome = checks.run_checks(canonical=canonical, adapted=adapted,
                                touched_ids={"b"}, profile={})
    assert outcome.passed is ok
