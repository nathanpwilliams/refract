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


def test_halt_causes_name_every_failing_gate():
    from app.services import checks
    from app.services import disposition as dispo

    outcome = checks.CheckOutcome(results=[
        checks.CheckResult("answer_key_integrity", "b4", False,
                           "answer key changed — 'a': True -> False"),
    ])
    causes = pipeline._halt_causes(
        outcome,
        [{"block_id": "b2", "issue": "fact_drift", "severity": "blocker",
          "detail": "invented a figure", "source": "verify_b"}],
        [dispo.Disposition("b1", dispo.HALT, "canonical is wrong for this role")],
    )

    assert len(causes) == 3
    assert "answer_key_integrity x1" in causes[0]
    assert "verify_b/fact_drift" in causes[1]
    assert "canonical is wrong for this role" in causes[2]


def test_no_causes_when_everything_is_clean():
    from app.services import checks
    assert pipeline._halt_causes(checks.CheckOutcome(), [], []) == []
