"""Cross-model verification (pipeline v2, §3 of the brief).

VERIFY runs TWICE over the same block pairs, at temperature 0, with two
different models. Findings are merged with a source tag, and the halt
rule is deliberately asymmetric:

    a 'blocker' from EITHER verifier halts the version.
    'advisory' findings feed the disposition stage instead.

Configure with VERIFY_MODEL_A / VERIFY_MODEL_B. Each value is
"<provider>:<model>" with the provider optional (defaults to anthropic),
so a second vendor becomes a registry entry plus an env change rather
than a pipeline change:

    VERIFY_MODEL_A=claude-sonnet-4-6
    VERIFY_MODEL_B=anthropic:claude-haiku-4-5

DECORRELATION CAVEAT — two Claude models are only PARTIALLY decorrelated.
They share training lineage, so a shared blind spot can survive both
passes; this stage raises the cost of a miss, it does not eliminate it.
A second-vendor verifier is the roadmap hardening, which is why the
interface below is provider-agnostic and the defaults are not.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from app.config import get_settings
from app.services import llm
from app.services import prompts

Completion = Callable[..., Awaitable[dict]]

DEFAULT_PROVIDER = "anthropic"


@dataclass(frozen=True)
class VerifierSpec:
    provider: str
    model: str
    label: str          # "verify_a" / "verify_b" — the finding's source tag


class Verifier(Protocol):
    spec: VerifierSpec

    async def verify(self, objectives: list, pairs: list[dict]) -> list[dict]:
        """Return findings, each tagged with its source."""


class AnthropicVerifier:
    """VERIFY against one Claude model."""

    def __init__(self, spec: VerifierSpec, complete: Completion | None = None,
                 temperature: float | None = None):
        self.spec = spec
        self._complete = complete or llm.complete_json
        self._temperature = temperature

    @property
    def temperature(self) -> float:
        if self._temperature is None:
            self._temperature = get_settings().verify_temperature
        return self._temperature

    async def verify(self, objectives: list, pairs: list[dict]) -> list[dict]:
        if not pairs:
            return []
        result = await self._complete(
            prompts.VERIFY_SYSTEM,
            prompts.VERIFY_USER_TEMPLATE.format(
                objectives_json=json.dumps(objectives),
                pairs_json=json.dumps(pairs),
            ),
            temperature=self.temperature,
            model=self.spec.model,
        )
        return tag_findings(result.get("findings") or [], self.spec)


# provider -> implementation. Adding "openai" / "gemini" means adding a
# class with the same two-method shape and one entry here.
_PROVIDERS: dict[str, type] = {"anthropic": AnthropicVerifier}


class UnknownProviderError(ValueError):
    pass


def parse_spec(raw: str, label: str) -> VerifierSpec:
    provider, separator, model = raw.partition(":")
    if not separator:
        provider, model = DEFAULT_PROVIDER, provider
    provider, model = provider.strip().lower(), model.strip()
    if provider not in _PROVIDERS:
        raise UnknownProviderError(
            f"{label}: unknown verifier provider {provider!r}; "
            f"supported: {sorted(_PROVIDERS)}")
    if not model:
        raise ValueError(f"{label}: no model named in {raw!r}")
    return VerifierSpec(provider=provider, model=model, label=label)


def tag_findings(findings: list, spec: VerifierSpec) -> list[dict]:
    """Attach the source tag, and drop anything that isn't a finding."""
    out = []
    for f in findings:
        if not isinstance(f, dict) or not f.get("block_id"):
            continue
        severity = f.get("severity")
        out.append({
            **f,
            # An unrecognized severity is treated as a blocker: a verifier
            # that can't say how bad something is must not be read as "fine".
            "severity": severity if severity in ("blocker", "advisory") else "blocker",
            "source": spec.label,
            "model": spec.model,
        })
    return out


def build_verifiers(complete: Completion | None = None) -> list[Verifier]:
    settings = get_settings()
    raw_a = settings.verify_model_a or settings.adaptation_model
    specs = [parse_spec(raw_a, "verify_a"),
             parse_spec(settings.verify_model_b, "verify_b")]
    return [_PROVIDERS[s.provider](s, complete) for s in specs]


def merge_findings(batches: list[list[dict]]) -> list[dict]:
    """Concatenate both verifiers' findings, blockers first.

    No de-duplication: when both verifiers independently flag the same
    block, that agreement is evidence worth keeping in the file.
    """
    merged = [f for batch in batches for f in batch]
    merged.sort(key=lambda f: (f.get("severity") != "blocker",
                               str(f.get("block_id")),
                               str(f.get("source"))))
    return merged


def blockers(findings: list[dict]) -> list[dict]:
    return [f for f in findings if f.get("severity") == "blocker"]


def findings_summary(findings: list[dict]) -> dict:
    by_source: dict[str, dict[str, int]] = {}
    for f in findings:
        tally = by_source.setdefault(str(f.get("source")),
                                     {"blocker": 0, "advisory": 0})
        tally[f.get("severity", "blocker")] += 1
    blocking = blockers(findings)
    return {
        "total": len(findings),
        "blockers": len(blocking),
        "by_source": by_source,
        "blocker_details": [
            {k: f.get(k) for k in ("block_id", "issue", "detail", "source", "model")}
            for f in blocking
        ],
    }
