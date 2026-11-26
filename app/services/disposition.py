"""Disposition stage (pipeline v2, §4 of the brief).

AI review replaces human per-block review. For each TOUCHED block the
stage sees the canonical text, the adapted text, the adaptation
instruction PLAN wrote for it, and every finding both verifiers raised,
and returns exactly one decision:

    accept              a genuine, warranted improvement for this audience
    revert_to_canonical gratuitous, harmful, or unwarranted -> canonical
                        text is restored on the adapted block
    halt                something reversion doesn't fix (rare, needs a reason)

NULL PREFERENCE: when in doubt, the canonical wins. That preference is
stated in the prompt and enforced here — a missing, malformed, or
unparseable decision becomes revert_to_canonical rather than accept, so a
model failure can only ever lose an adaptation, never smuggle one
through. Five production runs showed human review degenerating into
rubber-stamping; a default-accept here would rebuild exactly that.

The model is called once per lesson (touched blocks batched, as REWRITE
and VERIFY are) and returns per-block decisions. `complete` is injectable
so the decision logic is testable with stubbed model responses.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from app.config import get_settings
from app.services import llm
from app.services import prompts

ACCEPT = "accept"
REVERT = "revert_to_canonical"
HALT = "halt"
DECISIONS = (ACCEPT, REVERT, HALT)

# review_status written to block_revisions per decision.
REVIEW_STATUS = {ACCEPT: "auto_accepted", REVERT: "auto_reverted",
                 HALT: "auto_reverted"}
REVIEWER = "pipeline_v2"

Completion = Callable[..., Awaitable[dict]]


FALLBACK = "fallback"       # reverted in code, before this stage saw it
STAGE = "disposition"


@dataclass(frozen=True)
class Disposition:
    block_id: str
    decision: str
    reason: str
    unchanged_by_rewrite: bool = False
    decided_by: str = STAGE

    @property
    def review_status(self) -> str:
        return REVIEW_STATUS[self.decision]

    def to_dict(self) -> dict:
        return {"block_id": self.block_id, "decision": self.decision,
                "reason": self.reason, "decided_by": self.decided_by,
                "unchanged_by_rewrite": self.unchanged_by_rewrite}


def fallback_revert(block_id: str, causes: list[str]) -> Disposition:
    """The per-block fallback's record for a block reverted in code.

    A defective adaptation is reverted by the pipeline, not by asking a
    model what to do about it: the deterministic checks and the blocker
    rule have already decided, and the canonical text is the answer.
    """
    return Disposition(
        block_id, REVERT,
        "reverted by the per-block fallback — " + "; ".join(causes),
        decided_by=FALLBACK)


def normalize(raw: dict, block_id: str) -> Disposition:
    """One model entry -> one Disposition, null-preference enforced."""
    decision = raw.get("decision") if isinstance(raw, dict) else None
    reason = (raw.get("reason") if isinstance(raw, dict) else None) or ""
    if decision not in DECISIONS:
        return Disposition(
            block_id, REVERT,
            f"disposition stage returned no usable decision ({decision!r}); "
            "null preference applied — canonical retained"
            + (f" [model reason: {reason}]" if reason else ""))
    if decision == HALT and not reason.strip():
        # A halt must carry a stated reason; an unexplained halt is
        # downgraded to the reversible decision rather than stopping a
        # whole version on an unexplained verdict.
        return Disposition(
            block_id, REVERT,
            "disposition stage returned halt with no stated reason; "
            "downgraded to reversion — canonical retained")
    return Disposition(block_id, decision, reason.strip())


async def dispose_lesson(
    *,
    lesson_title: str,
    objectives: list,
    profile: dict,
    items: list[dict],
    findings: list[dict],
    complete: Completion | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> dict[str, Disposition]:
    """Decide every touched block in one lesson.

    items: [{block_id, block_type, adaptation_instruction, canonical, adapted}]
    Blocks REWRITE returned unchanged never reach the model — there is no
    delta to judge, and the canonical text already stands.
    """
    complete = complete or llm.complete_json
    if model is None or temperature is None:
        settings = get_settings()
        model = model or settings.adaptation_model
        temperature = (settings.disposition_temperature if temperature is None
                       else temperature)
    out: dict[str, Disposition] = {}

    judgeable = []
    for item in items:
        if item.get("unchanged"):
            out[item["block_id"]] = Disposition(
                item["block_id"], ACCEPT,
                "REWRITE returned the block unchanged; canonical text stands",
                unchanged_by_rewrite=True)
        else:
            judgeable.append(item)

    if not judgeable:
        return out

    by_block: dict[str, list] = {}
    for f in findings:
        by_block.setdefault(str(f.get("block_id")), []).append(f)

    payload = [
        {
            "block_id": item["block_id"],
            "block_type": item["block_type"],
            "adaptation_instruction": item.get("adaptation_instruction"),
            "canonical": item["canonical"],
            "adapted": item["adapted"],
            "findings": by_block.get(item["block_id"], []),
        }
        for item in judgeable
    ]

    result = await complete(
        prompts.DISPOSITION_SYSTEM,
        prompts.DISPOSITION_USER_TEMPLATE.format(
            audience_profile_json=json.dumps(profile),
            objectives_json=json.dumps(objectives),
            lesson_title=lesson_title,
            blocks_json=json.dumps(payload),
        ),
        temperature=temperature,
        model=model,
    )

    returned = {}
    for entry in (result.get("dispositions") or []):
        if isinstance(entry, dict) and entry.get("block_id"):
            returned[str(entry["block_id"])] = entry

    for item in judgeable:
        bid = item["block_id"]
        out[bid] = normalize(returned.get(bid, {}), bid)
    return out


def summary(dispositions: list[Disposition]) -> dict:
    counts = {ACCEPT: 0, REVERT: 0, HALT: 0}
    for d in dispositions:
        counts[d.decision] += 1
    return {
        "accepted": counts[ACCEPT],
        "reverted": counts[REVERT],
        "halted": counts[HALT],
        "reverted_by_fallback": sum(1 for d in dispositions
                                    if d.decision == REVERT and d.decided_by == FALLBACK),
        "reverted_by_stage": sum(1 for d in dispositions
                                 if d.decision == REVERT and d.decided_by == STAGE),
        "unchanged_by_rewrite": sum(1 for d in dispositions if d.unchanged_by_rewrite),
        "halt_reasons": [
            {"block_id": d.block_id, "reason": d.reason}
            for d in dispositions if d.decision == HALT
        ],
    }
