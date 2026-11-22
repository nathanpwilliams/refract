"""Cross-model verification: spec parsing, source tagging, and the halt rule.

The rule under test is asymmetric on purpose: a 'blocker' from EITHER
verifier halts the version, and no amount of agreement from the other one
clears it.
"""
import asyncio

import pytest

from app.services import verifiers
from app.services.verifiers import VerifierSpec

PAIRS = [{"block_id": "block-1", "original": {"markdown": "a"},
          "adapted": {"markdown": "b"}, "adaptation_instruction": "x"}]


def _stub(findings, record=None):
    async def complete(system, user, temperature, model=None):
        if record is not None:
            record.append({"temperature": temperature, "model": model})
        return {"lesson_verdict": "issues_found", "findings": findings}
    return complete


def _verify(findings, label="verify_a", model="model-x", record=None):
    spec = VerifierSpec("anthropic", model, label)
    v = verifiers.AnthropicVerifier(spec, _stub(findings, record), temperature=0.0)
    return asyncio.run(v.verify(["objective"], PAIRS))


# ---------------------------------------------------------------- spec parsing

@pytest.mark.parametrize("raw,provider,model", [
    ("claude-sonnet-4-6", "anthropic", "claude-sonnet-4-6"),
    ("anthropic:claude-haiku-4-5", "anthropic", "claude-haiku-4-5"),
    ("  ANTHROPIC : claude-opus-5 ", "anthropic", "claude-opus-5"),
])
def test_parse_spec_defaults_to_anthropic(raw, provider, model):
    spec = verifiers.parse_spec(raw, "verify_a")
    assert (spec.provider, spec.model, spec.label) == (provider, model, "verify_a")


def test_unknown_provider_is_a_clear_error():
    with pytest.raises(verifiers.UnknownProviderError) as exc:
        verifiers.parse_spec("openai:gpt-whatever", "verify_b")
    assert "verify_b" in str(exc.value)
    assert "anthropic" in str(exc.value)          # names what IS supported


def test_missing_model_is_rejected():
    with pytest.raises(ValueError):
        verifiers.parse_spec("anthropic:", "verify_a")


# ------------------------------------------------------------- source tagging

def test_findings_carry_their_source_and_model():
    findings = _verify(
        [{"block_id": "block-1", "issue": "fact_drift", "severity": "blocker",
          "detail": "added a figure"}],
        label="verify_b", model="claude-haiku-4-5")

    assert findings[0]["source"] == "verify_b"
    assert findings[0]["model"] == "claude-haiku-4-5"
    assert findings[0]["issue"] == "fact_drift"


def test_unparseable_findings_are_dropped_not_guessed():
    findings = _verify(["a string", {"issue": "fact_drift"}, None])
    assert findings == []                         # no block_id => not a finding


def test_unrecognized_severity_is_treated_as_a_blocker():
    findings = _verify([{"block_id": "block-1", "issue": "fact_drift",
                         "severity": "kinda bad", "detail": "?"}])
    assert findings[0]["severity"] == "blocker"
    assert verifiers.blockers(findings)


def test_no_pairs_means_no_api_call():
    record: list[dict] = []
    spec = VerifierSpec("anthropic", "model-x", "verify_a")
    v = verifiers.AnthropicVerifier(spec, _stub([], record), temperature=0.0)
    assert asyncio.run(v.verify(["objective"], [])) == []
    assert record == []


def test_verify_runs_at_the_configured_temperature_and_model():
    record: list[dict] = []
    _verify([], model="claude-haiku-4-5", record=record)
    assert record == [{"temperature": 0.0, "model": "claude-haiku-4-5"}]


# ----------------------------------------------------------------- the merge

def test_merge_keeps_both_verifiers_and_puts_blockers_first():
    a = [{"block_id": "block-2", "issue": "tone_violation",
          "severity": "advisory", "source": "verify_a"}]
    b = [{"block_id": "block-1", "issue": "fact_drift",
          "severity": "blocker", "source": "verify_b"}]
    merged = verifiers.merge_findings([a, b])

    assert [f["severity"] for f in merged] == ["blocker", "advisory"]
    assert len(merged) == 2


def test_agreement_is_not_deduplicated():
    # Both verifiers flagging the same block is evidence worth recording.
    same = {"block_id": "block-1", "issue": "fact_drift", "severity": "blocker"}
    merged = verifiers.merge_findings([[{**same, "source": "verify_a"}],
                                       [{**same, "source": "verify_b"}]])
    assert [f["source"] for f in merged] == ["verify_a", "verify_b"]


def test_a_single_blocker_from_either_verifier_halts():
    advisory_only = [{"block_id": "b", "issue": "tone_violation",
                      "severity": "advisory", "source": "verify_a"}]
    assert verifiers.blockers(advisory_only) == []

    one_sided = verifiers.merge_findings([
        advisory_only,
        [{"block_id": "b", "issue": "fact_drift", "severity": "blocker",
          "source": "verify_b"}],
    ])
    assert len(verifiers.blockers(one_sided)) == 1


def test_findings_summary_tallies_by_source():
    findings = verifiers.merge_findings([
        [{"block_id": "b1", "issue": "fact_drift", "severity": "blocker",
          "source": "verify_a", "model": "m-a", "detail": "d"}],
        [{"block_id": "b2", "issue": "tone_violation", "severity": "advisory",
          "source": "verify_b", "model": "m-b", "detail": "d"}],
    ])
    summary = verifiers.findings_summary(findings)

    assert summary["total"] == 2
    assert summary["blockers"] == 1
    assert summary["by_source"] == {"verify_a": {"blocker": 1, "advisory": 0},
                                    "verify_b": {"blocker": 0, "advisory": 1}}
    assert summary["blocker_details"][0]["block_id"] == "b1"


# -------------------------------------------------------- model-capability guard

def test_models_that_rejected_sampling_params_do_not_get_temperature():
    from app.services import llm
    assert llm.supports_temperature("claude-sonnet-4-6")
    assert llm.supports_temperature("claude-haiku-4-5")
    assert not llm.supports_temperature("claude-opus-5")
    assert not llm.supports_temperature("claude-sonnet-5")
