"""Pipeline glue between PLAN and the rest of the run (no API, no database).

PLAN is the stage that decides how much of the course a model ever sees,
so its output has to be resolved defensively: positions come back from a
model as ints, strings, or nonsense, and a block it should never have
selected must halt the run rather than be quietly dropped.
"""
from app.services import pipeline
from app.services.checks import BlockView
from app.services.pipeline import LessonRun

LESSON_ID = "11111111-1111-1111-1111-111111111111"


def _lesson() -> LessonRun:
    return LessonRun(
        lesson_id=LESSON_ID,
        target_lesson_id="target-lesson",
        title="How language models actually work",
        blocks=[
            BlockView("b1", 1, "heading", True, {"level": 2, "text": "Prediction, not lookup"}),
            BlockView("b2", 2, "text", True, {"markdown": "A worked example about RFIs."}),
            BlockView("b3", 3, "text", False, {"markdown": "Locked definitions block."}),
            BlockView("b4", 4, "knowledge_check", True, {
                "question": "Which is true?",
                "options": [{"id": "a", "text": "x", "correct": True, "feedback": "y"}],
                "style": "single"}),
        ],
    )


def _resolve(touch_list, lesson_id=LESSON_ID):
    plan = {"lessons": [{"lesson_id": lesson_id, "touch_list": touch_list}]}
    return pipeline._resolve_touch_list(plan, [_lesson()])


def test_a_sane_touch_list_maps_positions_to_canonical_block_ids():
    touch, locked, warnings = _resolve([
        {"block_position": 2, "reason": "RFI example is AEC-specific",
         "adaptation_instruction": "Transpose onto a status report."},
        {"block_position": 4, "reason": "framing",
         "adaptation_instruction": "Reframe the stem for managers."},
    ])

    assert set(touch[LESSON_ID]) == {"b2", "b4"}
    assert touch[LESSON_ID]["b2"]["adaptation_instruction"].startswith("Transpose")
    assert locked == set()
    assert warnings == []


def test_string_positions_from_the_model_are_coerced():
    touch, _, warnings = _resolve([
        {"block_position": "2", "reason": "r", "adaptation_instruction": "i"}])
    assert set(touch[LESSON_ID]) == {"b2"}
    assert warnings == []


def test_a_locked_block_is_not_rewritten_but_halts_the_run():
    touch, locked, warnings = _resolve([
        {"block_position": 3, "reason": "wants to reword the definitions",
         "adaptation_instruction": "Make it manager-friendly."}])

    # never sent to REWRITE: adaptable=false content stays away from models
    assert touch == {}
    # but the planning error is surfaced for locked_integrity to hard-fail
    assert locked == {"b3"}
    assert any("LOCKED" in w for w in warnings)


def test_unknown_positions_and_lessons_are_warned_not_silently_dropped():
    _, _, out_of_range = _resolve([
        {"block_position": 99, "reason": "r", "adaptation_instruction": "i"}])
    assert any("no such block" in w for w in out_of_range)

    _, _, non_numeric = _resolve([
        {"block_position": "the second one", "reason": "r",
         "adaptation_instruction": "i"}])
    assert any("non-numeric" in w for w in non_numeric)

    _, _, unknown_lesson = _resolve(
        [{"block_position": 2, "reason": "r", "adaptation_instruction": "i"}],
        lesson_id="99999999-9999-9999-9999-999999999999")
    assert any("not in this version" in w for w in unknown_lesson)


def test_a_touch_with_no_reason_or_instruction_is_skipped():
    touch, _, warnings = _resolve([{"block_position": 2}])
    assert touch == {}
    assert any("no reason or instruction" in w for w in warnings)


def test_reason_stands_in_when_the_instruction_is_missing():
    touch, _, _ = _resolve([
        {"block_position": 2, "reason": "the RFI example is AEC-specific"}])
    assert touch[LESSON_ID]["b2"]["adaptation_instruction"] == \
        "the RFI example is AEC-specific"


def test_malformed_plan_shapes_do_not_crash_the_run():
    for plan in ({}, {"lessons": None}, {"lessons": ["nope"]},
                 {"lessons": [{"lesson_id": LESSON_ID, "touch_list": None}]},
                 {"lessons": [{"lesson_id": LESSON_ID, "touch_list": ["nope"]}]}):
        touch, locked, _ = pipeline._resolve_touch_list(plan, [_lesson()])
        assert touch == {} and locked == set()


# -------------------------------------------------------- what PLAN is shown

def test_inventory_gives_plan_every_block_with_a_preview():
    inventory = pipeline._inventory([_lesson()])
    blocks = inventory["lessons"][0]["blocks"]

    assert [b["position"] for b in blocks] == [1, 2, 3, 4]
    assert blocks[2]["adaptable"] is False          # PLAN can see what's locked
    assert blocks[1]["preview"] == "A worked example about RFIs."
    assert blocks[1]["words"] == 5
    # assessment blocks preview their prose, not their answer key plumbing
    assert "Which is true?" in blocks[3]["preview"]


def test_previews_are_truncated_and_single_line():
    long_text = {"markdown": "word " * 200}
    preview = pipeline._preview(long_text)
    assert len(preview) == pipeline.PREVIEW_CHARS + 1    # + the ellipsis
    assert "\n" not in pipeline._preview({"markdown": "a\n\nb"})


# ------------------------------------------------------------- halt plumbing

def test_check_failures_become_blocker_findings_per_block():
    from app.services import checks

    outcome = checks.CheckOutcome(results=[
        checks.CheckResult("length_budget", "b2", False, "120 -> 200 words"),
        checks.CheckResult("structure_integrity", None, False, "sequence differs"),
        checks.CheckResult("verbatim_integrity", "b1", True, "fine"),
    ])
    by_block = pipeline._findings_from_checks(outcome)

    assert set(by_block) == {"b2"}                  # version-level has no block
    assert by_block["b2"][0]["severity"] == "blocker"
    assert by_block["b2"][0]["source"] == "checks"
    assert by_block["b2"][0]["issue"] == "length_budget"


# ------------------------------------------------------- per-block triage (§6)

def _outcome(*results):
    from app.services import checks
    return checks.CheckOutcome(results=list(results))


def test_a_touched_block_failing_a_revertible_check_is_reverted():
    from app.services import checks

    reverts, halts, unattributed = pipeline._triage(
        _outcome(checks.CheckResult("length_budget", "b2", False,
                                    "41 -> 58 words (+41%)")),
        [], touched_ids={"b2"})

    assert list(reverts) == ["b2"]
    assert "length_budget" in reverts["b2"][0]
    assert halts == [] and unattributed == []


def test_a_blocker_on_a_touched_block_is_reverted():
    reverts, halts, _ = pipeline._triage(
        _outcome(),
        [{"block_id": "b2", "issue": "fact_drift", "severity": "blocker",
          "detail": "invented a figure", "source": "verify_b"},
         {"block_id": "b2", "issue": "tone_violation", "severity": "advisory",
          "detail": "slightly brisk", "source": "verify_a"}],
        touched_ids={"b2"})

    assert reverts["b2"] == ["verify_b/fact_drift: invented a figure"]
    assert halts == []          # advisories never revert on their own


def test_halting_checks_are_never_reverted():
    from app.services import checks

    for check in sorted(checks.HALTING_CHECKS):
        reverts, halts, _ = pipeline._triage(
            _outcome(checks.CheckResult(check, "b2", False, "broken")),
            [], touched_ids={"b2"})
        assert reverts == {}, check
        assert len(halts) == 1, check


def test_a_failure_on_an_untouched_block_halts_even_if_revertible():
    from app.services import checks

    reverts, halts, _ = pipeline._triage(
        _outcome(checks.CheckResult("answer_key_integrity", "b9", False,
                                    "answer key changed")),
        [], touched_ids={"b2"})
    assert reverts == {}
    assert "answer_key_integrity" in halts[0]


def test_a_version_level_failure_halts():
    from app.services import checks

    _, halts, _ = pipeline._triage(
        _outcome(checks.CheckResult("structure_integrity", None, False,
                                    "block sequence differs")),
        [], touched_ids={"b2"})
    assert "block sequence differs" in halts[0]


def test_a_blocker_naming_an_untouched_block_is_set_aside():
    reverts, halts, unattributed = pipeline._triage(
        _outcome(),
        [{"block_id": "made-up", "issue": "fact_drift", "severity": "blocker",
          "detail": "about nothing", "source": "verify_a"}],
        touched_ids={"b2"})

    assert reverts == {} and halts == []
    assert "was not touched" in unattributed[0]


def test_halt_causes_name_every_condition_a_reversion_cannot_cure():
    from app.services import checks
    from app.services import disposition as dispo

    causes = pipeline._halt_causes(
        integrity_halts=["locked_integrity: locked block differs"],
        revert_failures=[checks.CheckResult("revert_integrity", "b2", False,
                                            "reversion did not take")],
        planned=3, surviving=0,
        halt_dispositions=[dispo.Disposition("b1", dispo.HALT,
                                             "canonical is wrong for this role")])

    assert len(causes) == 4
    assert "locked_integrity" in causes[0]
    assert "not byte-identical" in causes[1]
    assert "all 3 were reverted" in causes[2]
    assert "canonical is wrong for this role" in causes[3]


def test_surviving_adaptations_mean_no_halt():
    assert pipeline._halt_causes(
        integrity_halts=[], revert_failures=[], planned=3, surviving=2,
        halt_dispositions=[]) == []


def test_zero_planned_adaptations_halts_with_its_own_reason():
    causes = pipeline._halt_causes(
        integrity_halts=[], revert_failures=[], planned=0, surviving=0,
        halt_dispositions=[])
    assert causes == ["no adaptations survived: PLAN touched no blocks"]
