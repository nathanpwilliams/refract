"""Thin Claude API wrapper enforcing the pipeline's JSON-only contract.

Every pipeline prompt demands raw JSON. Models occasionally wrap output in
```json fences or add a stray preamble; complete_json strips fences, parses,
and retries with an explicit correction message on failure. Uses the official
anthropic SDK (https://docs.claude.com/en/api/overview).
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


async def complete_json(system: str, user: str, temperature: float) -> dict:
    settings = get_settings()
    messages = [{"role": "user", "content": user}]
    last_error = ""

    for attempt in range(settings.llm_max_retries + 1):
        response = await client().messages.create(
            model=settings.adaptation_model,
            max_tokens=settings.max_output_tokens,
            temperature=temperature,
            system=system,
            messages=messages,
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
