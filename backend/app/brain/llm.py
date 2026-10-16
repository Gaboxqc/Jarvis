"""LLM client — REQ-27, REQ-30.

Talks to Ollama over its HTTP API. No SDK dependency, so the same ~100 lines
cover any OpenAI-compatible endpoint later.

Structured output is requested via Ollama's `format: json` rather than native
tool-calling. That is a deliberate compatibility choice: tool-calling support
varies sharply by model -- the default qwen2.5 advertises it, llama3 does not --
and a router that only works on some models is a router that breaks when the
user changes a line of config. JSON mode is supported by every model Ollama
serves, so it is the floor everything else stands on.

Native tool-calling exists alongside it (see `tools=` below) and is switched
off, because it was measured against this and lost: 89/96 versus 95/96 on
qwen2.5 with the same worked examples in both prompts. JSON mode is not the
fallback here — it is the better mechanism for this workload, and the one every
model Ollama serves can follow.
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
    _chosen.clear()


# -- which model actually answers -----------------------------------------
#
# `brain.model` names one model, and if it is not pulled every single turn
# fails with "run ollama pull qwen2.5". That is the most common first-run
# failure there is -- the README spends a paragraph on it -- and it happens
# with a perfectly good model sitting right there, because the configured name
# did not match it.
#
# So: use what is installed, say which one, and fail only when there is
# genuinely nothing to use.
#
# The substitution is not silent. It changes behaviour -- routing accuracy is
# model-specific, and this project measured an 88%/62% gap between two of the
# names below -- so /health reports the configured model, the model in use and
# whether they differ, the events stream carries it, and the window says so.
# "Working, and here is what changed" is the honest version; refusing to start
# on a machine that can run is not.
#
# Order is the measured one where there is a measurement (qwen2.5 beat llama3
# 88% to 62% on held-out routing, at the same speed) and a reasonable guess
# after that. Anything installed beats nothing.
PREFERRED_MODELS = (
    "qwen2.5",
    "qwen2",
    "llama3.1",
    "llama3.2",
    "llama3",
    "mistral",
    "gemma2",
    "phi3",
)

# configured name -> the name to send. Resolved once and refreshed by health(),
# which the events stream already calls every ten seconds, so a turn never pays
# for the lookup.
_chosen: dict[str, str] = {}


def _is(installed: str, model: str) -> bool:
    """Whether an installed name is this model. Ollama reports tags."""
    return installed == model or installed.startswith(f"{model}:")


def _installed_models(settings: BrainSettings) -> list[str] | None:
    """What Ollama has, or None if it could not be asked.

    None and [] are different answers and the caller treats them differently:
    an unreachable Ollama must not look like an empty one, or a connection
    problem would be reported as "no models installed" and send somebody off
    to download one they already have.
    """
    try:
        response = httpx.get(f"{settings.ollama_host.rstrip('/')}/api/tags", timeout=5.0)
        response.raise_for_status()
        return [str(entry.get("name", "")) for entry in response.json().get("models", [])]
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        return None


def choose_model(settings: BrainSettings, installed: list[str] | None) -> str:
    """The model to send, given what is installed. Pure, so it is testable."""
    if installed is None:
        # Cannot tell. Send what was asked for and let the call report the real
        # problem, which is that Ollama is not answering.
        return settings.model
    if any(_is(name, settings.model) for name in installed):
        return settings.model

    for preferred in PREFERRED_MODELS:
        for name in installed:
            if _is(name, preferred):
                return name

    # Nothing recognised. Any model beats no model; sorted so two machines with
    # the same models make the same choice.
    others = sorted(name for name in installed if name)
    if others:
        return others[0]

    # Genuinely nothing pulled. Returning the configured name means the failure
    # says "run ollama pull <model>", which is the right instruction.
    return settings.model


def active_model(settings: BrainSettings | None = None) -> str:
    """The model this process will actually send to, configured or substituted."""
    settings = settings or load_config().brain
    cached = _chosen.get(settings.model)
    if cached is not None:
        return cached
    return _resolve_model(settings)


def _resolve_model(settings: BrainSettings) -> str:
    chosen = choose_model(settings, _installed_models(settings))
    previous = _chosen.get(settings.model)
    _chosen[settings.model] = chosen
    if chosen != settings.model and previous != chosen:
        # Once per change, not once per turn.
        log.warning(
            "the configured model %r is not installed; using %r instead. "
            "Run `ollama pull %s` to use the one you asked for, or set "
            "brain.model to %r.",
            settings.model, chosen, settings.model, chosen,
        )
    return chosen


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
        # Not settings.model: the configured name may not be installed, and
        # answering with what is there beats failing every turn. See
        # active_model().
        "model": active_model(settings),
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
        # Not settings.model: the configured name may not be installed, and
        # answering with what is there beats failing every turn. See
        # active_model().
        "model": active_model(settings),
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
    """Whether the brain is reachable, for the CLI banner and /health.

    Also where the model choice is refreshed. The events stream calls this
    every ten seconds, so `active_model()` is answered from cache during a
    turn and a model pulled while the app is running is picked up without a
    restart.

    `ok` means "a turn will get an answer", which is the question anyone
    checking this is actually asking. It is true when the configured model is
    installed and also when something else is -- with `model`, `configured`
    and `substituted` saying which, because an assistant quietly answering
    from a model you did not choose is the thing this must not be.
    """
    settings = load_config().brain
    models = _installed_models(settings)
    if models is None:
        return {
            "ok": False,
            "error": f"can't reach Ollama at {settings.ollama_host}",
            "host": settings.ollama_host,
            "model": settings.model,
            "configured": settings.model,
            "substituted": False,
            "model_installed": False,
            "models": [],
        }

    chosen = _resolve_model(settings)
    configured_installed = any(_is(name, settings.model) for name in models)
    usable = bool(models) and (configured_installed or chosen != settings.model)
    substituted = usable and not configured_installed

    if substituted:
        error = (
            f"'{settings.model}' isn't installed, so I'm using '{chosen}'. "
            f"Run `ollama pull {settings.model}` for the one you configured."
        )
    elif usable:
        error = None
    else:
        error = f"model '{settings.model}' not installed"

    return {
        "ok": usable,
        "host": settings.ollama_host,
        # What will actually answer, which is what every caller of this wants
        # to know. `configured` is kept beside it rather than instead of it:
        # the Settings screen shows what the file says, and this says what the
        # file got.
        "model": chosen,
        "configured": settings.model,
        "substituted": substituted,
        "model_installed": configured_installed,
        "models": models,
        "error": error,
    }
