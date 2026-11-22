"""Disposition-stage tests with stubbed model responses (no API, no database).

The stage's job is to decide what ships. What these tests pin down is the
NULL PREFERENCE: the canonical wins every tie, and every way the model can
fail to give a usable answer resolves to revert_to_canonical rather than
accept. A default-accept here would rebuild the rubber-stamping that
pipeline v2 exists to remove.

Coroutines are driven with asyncio.run rather than an async-test plugin —
the suite stays runnable with plain pytest.
"""
import asyncio
import json

import pytest

from app.services import disposition as dispo

CANONICAL = {"markdown": (
    "Verify any figure you did not produce yourself before you pass it on. "
    "The sign-off is yours regardless of who drafted the text.")}

# Gratuitous synonym churn: different words, identical meaning. This is the
# failure mode five production runs surfaced.
CHURNED = {"markdown": (
    "Check any number you did not generate yourself before you forward it. "
    "The approval is yours no matter who wrote the text.")}

# A genuine adaptation: same facts, this audience's artifact and failure mode.
ADAPTED = {"markdown": (
    "Verify any figure you did not produce yourself before you pass it "
    "upward — including a variance number inside a direct report's status "
    "summary. The sign-off is yours regardless of who, or what, drafted it.")}

OBJECTIVES = ["Apply verification practices before relying on AI output"]
PROFILE = {"name": "managers",
           "ai_failure_modes": ["Sending AI-polished summaries upward unverified"]}


def _item(block_id="block-1", adapted=None, unchanged=False):
    return {
        "block_id": block_id,
        "block_type": "text",
        "adaptation_instruction": "Transpose onto a status-report review.",
        "canonical": CANONICAL,
        "adapted": adapted if adapted is not None else ADAPTED,
        "unchanged": unchanged,
    }


def _stub(response, record):
    """A complete_json stand-in returning a canned payload."""
    async def complete(system, user, temperature, model=None):
        record.append({"system": system, "user": user,
                       "temperature": temperature, "model": model})
        return response
    return complete


def dispose(response, items=None, findings=None, record=None):
    """Run the stage against a canned model response."""
    return asyncio.run(dispo.dispose_lesson(
        lesson_title="Verify before you rely",
        objectives=OBJECTIVES,
        profile=PROFILE,
        items=[_item()] if items is None else items,
        findings=findings or [],
        complete=_stub(response, record if record is not None else []),
        model="stub-model",
        temperature=0.0,
    ))


# ------------------------------------------------ the brief's acceptance case

def test_gratuitous_synonym_churn_reverts_to_canonical():
    decided = dispose(
        {"dispositions": [{
            "block_id": "block-1",
            "decision": "revert_to_canonical",
            "reason": ("near-zero semantic delta: 'figure'->'number' and "
                       "'sign-off'->'approval' change wording, not meaning "
                       "for this audience"),
        }]},
        items=[_item(adapted=CHURNED)],
    )

    d = decided["block-1"]
    assert d.decision == dispo.REVERT
    assert d.review_status == "auto_reverted"
    assert "semantic delta" in d.reason
    assert not d.unchanged_by_rewrite


def test_warranted_adaptation_is_accepted():
    decided = dispose({"dispositions": [{
        "block_id": "block-1",
        "decision": "accept",
        "reason": ("names the variance figure inside a direct report's "
                   "summary, this role's actual exposure"),
    }]})

    d = decided["block-1"]
    assert d.decision == dispo.ACCEPT
    assert d.review_status == "auto_accepted"


# --------------------------------------------- null preference under failure

@pytest.mark.parametrize("payload", [
    {},                                                  # no dispositions key
    {"dispositions": None},
    {"dispositions": []},                                # block not mentioned
    {"dispositions": [{"block_id": "other", "decision": "accept"}]},
    {"dispositions": [{"block_id": "block-1"}]},         # no decision
    {"dispositions": [{"block_id": "block-1", "decision": "looks fine"}]},
    {"dispositions": [{"block_id": "block-1", "decision": None}]},
    {"dispositions": [{"block_id": "block-1", "decision": "accepted"}]},  # near miss
    {"dispositions": ["not an object"]},
])
def test_unusable_model_output_reverts_rather_than_accepts(payload):
    d = dispose(payload)["block-1"]
    assert d.decision == dispo.REVERT, payload
    assert "null preference applied" in d.reason


def test_model_reason_is_kept_when_the_decision_is_unusable():
    d = dispose({"dispositions": [{
        "block_id": "block-1", "decision": "maybe",
        "reason": "I could not tell whether this helps"}]})["block-1"]
    assert d.decision == dispo.REVERT
    assert "I could not tell whether this helps" in d.reason


def test_halt_requires_a_stated_reason():
    halting = dispose({"dispositions": [{
        "block_id": "block-1", "decision": "halt",
        "reason": "the canonical claim is wrong for regulated industries"}]})
    assert halting["block-1"].decision == dispo.HALT
    assert halting["block-1"].review_status == "auto_reverted"

    unexplained = dispose({"dispositions": [{
        "block_id": "block-1", "decision": "halt", "reason": "  "}]})
    assert unexplained["block-1"].decision == dispo.REVERT
    assert "no stated reason" in unexplained["block-1"].reason


# ------------------------------------------------------ what reaches the model

def test_blocks_rewrite_left_unchanged_never_reach_the_model():
    record: list[dict] = []
    decided = dispose(
        {"dispositions": []},
        items=[_item("block-1", adapted=CANONICAL, unchanged=True)],
        record=record,
    )

    assert record == []                      # no API call at all
    d = decided["block-1"]
    assert d.decision == dispo.ACCEPT        # canonical text already stands
    assert d.unchanged_by_rewrite


def test_findings_are_routed_to_their_own_block():
    record: list[dict] = []
    dispose(
        {"dispositions": [{"block_id": "block-1", "decision": "accept",
                           "reason": "ok"},
                          {"block_id": "block-2", "decision": "accept",
                           "reason": "ok"}]},
        items=[_item("block-1"), _item("block-2")],
        findings=[
            {"block_id": "block-1", "issue": "unwarranted_change",
             "severity": "advisory", "detail": "wording only", "source": "verify_a"},
            {"block_id": "block-2", "issue": "tone_violation",
             "severity": "advisory", "detail": "condescending", "source": "verify_b"},
        ],
        record=record,
    )

    assert len(record) == 1
    body = record[0]["user"]
    payload = json.loads(
        body.split("<lesson title=", 1)[1].split(">", 1)[1]
            .rsplit("</lesson>", 1)[0])
    by_block = {p["block_id"]: p for p in payload}
    assert [f["issue"] for f in by_block["block-1"]["findings"]] == ["unwarranted_change"]
    assert [f["issue"] for f in by_block["block-2"]["findings"]] == ["tone_violation"]
    # every block the model judges carries its canonical, its adapted text,
    # and the instruction it was rewritten under
    assert by_block["block-1"]["canonical"] == CANONICAL
    assert by_block["block-1"]["adapted"] == ADAPTED
    assert by_block["block-1"]["adaptation_instruction"]
    # and the null preference is in what the model actually sees
    assert "THE CANONICAL WINS" in record[0]["system"]
    assert record[0]["temperature"] == 0.0
    assert record[0]["model"] == "stub-model"


def test_no_judgeable_blocks_makes_no_call():
    record: list[dict] = []
    decided = dispose({"dispositions": []}, items=[], record=record)
    assert decided == {}
    assert record == []


# ------------------------------------------------------------------- summary

def test_summary_counts_every_decision():
    dispositions = [
        dispo.Disposition("b1", dispo.ACCEPT, "earns it"),
        dispo.Disposition("b2", dispo.ACCEPT, "unchanged", unchanged_by_rewrite=True),
        dispo.Disposition("b3", dispo.REVERT, "churn"),
        dispo.Disposition("b4", dispo.HALT, "canonical is wrong here"),
    ]
    assert dispo.summary(dispositions) == {
        "accepted": 2, "reverted": 1, "halted": 1,
        "unchanged_by_rewrite": 1,
        "halt_reasons": [{"block_id": "b4", "reason": "canonical is wrong here"}],
    }
