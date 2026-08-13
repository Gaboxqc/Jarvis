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
from collections.abc import Iterator
from dataclasses import dataclass, field
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
    # Populated only when the call passed `tools` and the model used them.
    # Same shape either way -- [{"name": ..., "args": {...}}] -- so callers do
    # not branch on which mechanism produced the answer.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def as_json(self) -> dict[str, Any] | None:
        return extract_json(self.text)


_capabilities: dict[str, frozenset[str]] = {}


def supports_tools(settings: BrainSettings | None = None) -> bool:
    """Whether this model can be given tool definitions natively.

    Asked once per model and cached. A model that cannot do this is not a
    problem -- routing falls back to the JSON prompt, which every model Ollama
    serves can follow -- so a failure to answer is treated as "no" rather than
    raised. Being wrong here costs accuracy, not correctness.
    """
    settings = settings or load_config().brain
    if settings.model not in _capabilities:
        try:
            response = httpx.post(
                f"{settings.ollama_host.rstrip('/')}/api/show",
                json={"model": settings.model},
                timeout=10.0,
            )
            response.raise_for_status()
            found = response.json().get("capabilities") or []
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            log.info("could not read capabilities for %s; assuming no tool support",
                     settings.model)
            found = []
        _capabilities[settings.model] = frozenset(found)
    return "tools" in _capabilities[settings.model]


def reset_capability_cache() -> None:
    """Test hook, and the escape hatch if a model is swapped under a running app."""
    _capabilities.clear()


def chat(
    messages: list[dict[str, str]],
    *,
    json_mode: bool = False,
    temperature: float | None = None,
    settings: BrainSettings | None = None,
    tools: list[dict[str, Any]] | None = None,
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
    if tools:
        payload["tools"] = tools
    elif json_mode:
        # Never both. Constraining output to JSON while also asking for tool
        # calls makes the model emit a JSON *description* of a call instead of
        # calling anything, and the tool_calls list comes back empty.
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

    message = body.get("message") or {}
    text = (message.get("content") or "").strip()
    return LLMReply(text=text, raw=body, tool_calls=_read_tool_calls(message))


def stream(
    messages: list[dict[str, str]],
    *,
    temperature: float | None = None,
    settings: BrainSettings | None = None,
) -> Iterator[str]:
    """Yield the reply in pieces as the model produces them.

    Only prose is streamed. Routing is not: it returns JSON that has to be
    parsed and validated as a whole before anything acts on it, and there is
    nothing to show a user in a half-written tool call.

    Errors are raised as LLMUnavailable exactly as in chat(), including partway
    through a stream. A caller that has already emitted some text has to decide
    what to do with it; the orchestrator keeps what arrived and appends the
    failure, because silently truncating a reply looks like the model finished.
    """
    settings = settings or load_config().brain
    payload: dict[str, Any] = {
        "model": settings.model,
        "messages": messages,
        "stream": True,
        "options": {
            "temperature": settings.temperature if temperature is None else temperature,
            "num_ctx": settings.context_tokens,
        },
    }
    _warn_if_oversized(messages, settings)

    url = f"{settings.ollama_host.rstrip('/')}/api/chat"
    try:
        with httpx.stream("POST", url, json=payload, timeout=settings.timeout_seconds) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.strip():
                    continue
                try:
                    body = json.loads(line)
                except json.JSONDecodeError:
                    # A malformed frame mid-stream is not worth failing the whole
                    # reply over; the next one is usually fine.
                    log.debug("unparseable stream frame: %r", line[:120])
                    continue
                piece = ((body.get("message") or {}).get("content") or "")
                if piece:
                    yield piece
                if body.get("done"):
                    return
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
        if exc.response.status_code == 404:
            raise LLMUnavailable(
                f"The model '{settings.model}' isn't installed. Run: ollama pull {settings.model}"
            ) from exc
        raise LLMUnavailable("The model returned an error while streaming.") from exc
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"The model call failed: {exc}") from exc


def _read_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise Ollama's tool_calls into [{"name", "args"}].

    Arguments come back as an object from Ollama, but some models emit them as
    a JSON string instead, so both are handled. Anything unparseable is dropped
    rather than guessed at -- the caller treats an empty list as "the model did
    not route", which degrades to a conversational answer.
    """
    calls = []
    for entry in message.get("tool_calls") or []:
        function = (entry or {}).get("function") or {}
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        raw_args = function.get("arguments")
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except json.JSONDecodeError:
                raw_args = {}
        calls.append({"name": name, "args": raw_args if isinstance(raw_args, dict) else {}})
    return calls


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
