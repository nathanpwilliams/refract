"""Thin Claude API wrapper enforcing the pipeline's JSON-only contract.

Every pipeline prompt demands raw JSON. Models occasionally wrap output in
```json fences or add a stray preamble; complete_json strips fences, parses,
and retries with an explicit correction message on failure. Uses the official
anthropic SDK (https://docs.claude.com/en/api/overview).

complete_json takes an optional per-call model so pipeline v2 can run the
same prompt against two different models (cross-model verification); it
defaults to settings.adaptation_model.
"""
import json

import anthropic

from app.config import get_settings

_client: anthropic.AsyncAnthropic | None = None


def client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key)
    return _client


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    # tolerate a short preamble before the JSON object
    first = text.find("{")
    last = text.rfind("}")
    if first > 0 and last != -1:
        text = text[first : last + 1]
    return text.strip()


class LLMContractError(Exception):
    """Model failed to return parseable JSON after all retries."""


# Models that removed the sampling parameters: temperature/top_p/top_k are
# rejected with a 400. Pipeline v2 lets any Claude model be configured as a
# verifier by env, so the temperature we ask for has to be droppable rather
# than fatal. (Adaptive thinking replaced sampling control on these.)
_NO_SAMPLING_PREFIXES = (
    "claude-opus-5", "claude-opus-4-7", "claude-opus-4-8",
    "claude-sonnet-5", "claude-fable-5", "claude-mythos-5",
)


def supports_temperature(model: str) -> bool:
    return not model.startswith(_NO_SAMPLING_PREFIXES)


async def complete_json(system: str, user: str, temperature: float,
                        model: str | None = None) -> dict:
    settings = get_settings()
    model = model or settings.adaptation_model
    messages = [{"role": "user", "content": user}]
    last_error = ""
    sampling = {"temperature": temperature} if supports_temperature(model) else {}

    for attempt in range(settings.llm_max_retries + 1):
        response = await client().messages.create(
            model=model,
            max_tokens=settings.max_output_tokens,
            system=system,
            messages=messages,
            **sampling,
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            return json.loads(_strip_fences(text))
        except json.JSONDecodeError as exc:
            last_error = str(exc)
            messages = messages + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": (
                        f"Your previous response was not valid JSON ({exc}). "
                        "Respond again with ONLY the corrected JSON object — "
                        "no prose, no markdown fences."
                    ),
                },
            ]

    raise LLMContractError(f"Malformed JSON after retries: {last_error}")
