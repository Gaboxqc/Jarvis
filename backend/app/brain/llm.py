"""LLM client — REQ-27, REQ-30.

Talks to Ollama over its HTTP API. No SDK dependency, so the same ~100 lines
cover any OpenAI-compatible endpoint later.

Structured output is requested via Ollama's `format: json` rather than native
tool-calling. That is a deliberate compatibility choice: tool-calling support
varies sharply by model -- the default qwen2.5 advertises it, llama3 does not --
and a router that only works on some models is a router that breaks when the
user changes a line of config. JSON mode is supported by every model Ollama
serves, so it is the floor everything else stands on.

Using native tool-calling where the model offers it, and falling back to this,
is T12.6 and not yet done.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ..settings import BrainSettings, load_config

log = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    """The model could not be reached or did not answer in time."""


@dataclass
class LLMReply:
    text: str
    raw: dict[str, Any]

    def as_json(self) -> dict[str, Any] | None:
        return extract_json(self.text)


def chat(
    messages: list[dict[str, str]],
    *,
    json_mode: bool = False,
    temperature: float | None = None,
    settings: BrainSettings | None = None,
) -> LLMReply:
    settings = settings or load_config().brain
    payload: dict[str, Any] = {
        "model": settings.model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": settings.temperature if temperature is None else temperature,
            "num_ctx": settings.context_tokens,
        },
    }
    if json_mode:
        payload["format"] = "json"

    _warn_if_oversized(messages, settings)

    url = f"{settings.ollama_host.rstrip('/')}/api/chat"
    try:
        response = httpx.post(url, json=payload, timeout=settings.timeout_seconds)
        response.raise_for_status()
        body = response.json()
    except httpx.ConnectError as exc:
        raise LLMUnavailable(
            f"I can't reach the language model at {settings.ollama_host}. "
            "Is Ollama running? (`ollama serve`)"
        ) from exc
    except httpx.TimeoutException as exc:
        raise LLMUnavailable(
            f"The model took longer than {settings.timeout_seconds}s and I gave up on it."
        ) from exc
    except httpx.HTTPStatusError as exc:
        detail = _error_detail(exc.response)
        if exc.response.status_code == 404:
            raise LLMUnavailable(
                f"The model '{settings.model}' isn't installed. Run: ollama pull {settings.model}"
            ) from exc
        raise LLMUnavailable(f"The model returned an error: {detail}") from exc
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise LLMUnavailable(f"The model call failed: {exc}") from exc

    text = ((body.get("message") or {}).get("content") or "").strip()
    return LLMReply(text=text, raw=body)


def estimate_tokens(messages: list[dict[str, str]]) -> int:
    """Rough token count for a message list.

    Deliberately approximate — this exists to catch a prompt that is twice the
    context window, not to budget the last hundred tokens. Four characters per
    token is the usual rule of thumb and is close enough for that job without
    dragging a tokenizer into the runtime.
    """
    return sum(len(m.get("content") or "") for m in messages) // 4


def _warn_if_oversized(messages: list[dict[str, str]], settings: BrainSettings) -> None:
    """Say something when the prompt cannot fit.

    Ollama's response to an over-long prompt is to drop the overflow and answer
    anyway, so an oversized prompt looks exactly like a working one — it just
    produces worse answers forever. This is the only warning anyone gets.
    """
    estimate = estimate_tokens(messages)
    # Leave room for the reply; a prompt that fills the window leaves nowhere
    # to generate into.
    budget = int(settings.context_tokens * 0.75)
    if estimate > budget:
        log.warning(
            "prompt is ~%d tokens against a %d-token context: Ollama will silently "
            "truncate it and the answer will be based on part of the input. "
            "Raise brain.context_tokens or shorten the prompt.",
            estimate,
            settings.context_tokens,
        )


def _error_detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", response.text))[:200]
    except (json.JSONDecodeError, ValueError):
        return response.text[:200]


def extract_json(text: str) -> dict[str, Any] | None:
    """Pull a JSON object out of a model reply.

    Even in JSON mode, smaller models wrap output in prose or fences often enough
    that parsing has to be forgiving. Everything downstream treats a `None` here
    as "the model did not route", not as an error.
    """
    if not text:
        return None

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    fenced = text
    if "```" in fenced:
        segments = fenced.split("```")
        for segment in segments:
            candidate = segment.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{"):
                fenced = candidate
                break

    start = fenced.find("{")
    end = fenced.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(fenced[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def health() -> dict[str, Any]:
    """Whether the brain is reachable, for the CLI banner and /health."""
    settings = load_config().brain
    try:
        response = httpx.get(f"{settings.ollama_host.rstrip('/')}/api/tags", timeout=5.0)
        response.raise_for_status()
        models = [m.get("name", "") for m in response.json().get("models", [])]
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc), "host": settings.ollama_host, "models": []}

    installed = any(m == settings.model or m.startswith(f"{settings.model}:") for m in models)
    return {
        "ok": installed,
        "host": settings.ollama_host,
        "model": settings.model,
        "model_installed": installed,
        "models": models,
        "error": None if installed else f"model '{settings.model}' not installed",
    }
