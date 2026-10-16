"""Answering with the model that is there — REQ-27, REQ-30.

`brain.model` names one model. If it is not pulled, every single turn used to
fail with "run ollama pull qwen2.5" -- on a machine that may well have llama3
sitting right there, unused, because the configured name did not match it. That
is the most common first-run failure this app has, and the README spends a
paragraph on it.

So the model is chosen from what is installed. The part that needs testing is
not the choosing, which is a sorted list; it is that the substitution is
*visible*. An assistant quietly answering from a model the user did not pick is
worse than one that refuses: routing accuracy is model-specific -- this project
measured 88% against 62% between two of the names in the preference list -- so
the answers get worse in a way nothing explains.

Nothing here talks to Ollama. `choose_model` is pure, and the rest is stubbed.
"""

from __future__ import annotations

import pytest
import yaml

from app.brain import llm
from app.settings import BrainSettings, load_config, reset_config_cache


@pytest.fixture(autouse=True)
def forget_choices():
    llm.reset_capability_cache()
    yield
    llm.reset_capability_cache()


def settings(model: str = "qwen2.5") -> BrainSettings:
    return BrainSettings(model=model)


def configure_model(config_file, name: str) -> BrainSettings:
    """Name a model in the config file, as a person would.

    `health()` reads the live config rather than taking a settings argument --
    it answers "what is this process doing", and a parameter would let a test
    assert about a configuration the app was not in.
    """
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    raw["brain"]["model"] = name
    config_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    reset_config_cache()
    llm.reset_capability_cache()
    return load_config().brain


# -- choosing ------------------------------------------------------------


def test_the_configured_model_is_used_when_it_is_installed():
    assert llm.choose_model(settings(), ["llama3:latest", "qwen2.5:latest"]) == "qwen2.5"


def test_a_tag_still_counts_as_installed():
    """Ollama reports `qwen2.5:latest`, and the config says `qwen2.5`. Those
    are the same model, and treating them as different is the whole bug."""
    assert llm.choose_model(settings(), ["qwen2.5:7b"]) == "qwen2.5"


def test_the_preferred_installed_model_is_chosen():
    """Order is the measured one: qwen2.5 beat llama3 88% to 62% on held-out
    routing at the same speed, so where both are present it is not a toss-up."""
    chosen = llm.choose_model(settings("mistral-nemo"), ["llama3:latest", "qwen2.5:latest"])
    assert chosen == "qwen2.5:latest"


def test_an_unrecognised_model_is_better_than_none():
    """Something installed beats nothing installed, even if it is not on the
    list -- and sorted, so two machines with the same models agree."""
    chosen = llm.choose_model(settings(), ["zephyr:latest", "aya:8b"])
    assert chosen == "aya:8b"


def test_nothing_installed_leaves_the_configured_name():
    """So the failure says `ollama pull qwen2.5`, which is the instruction
    somebody can act on, rather than naming a model they never asked for."""
    assert llm.choose_model(settings(), []) == "qwen2.5"


def test_an_unreachable_ollama_is_not_an_empty_one():
    """None and [] are different answers.

    Treating a connection failure as "no models installed" would send someone
    off to download a model they already have, and hide the actual problem.
    """
    assert llm.choose_model(settings(), None) == "qwen2.5"


# -- what the turn sends -------------------------------------------------


def test_a_turn_is_sent_to_the_substituted_model(workspace, monkeypatch):
    """The whole point. Without this the choice is a number in a log line."""
    monkeypatch.setattr(llm, "_installed_models", lambda s: ["llama3:latest"])

    sent: dict[str, object] = {}

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"message": {"content": "Hello."}}

    def fake_post(url, json=None, timeout=None):
        sent.update(json or {})
        return Response()

    monkeypatch.setattr(llm.httpx, "post", fake_post)

    reply = llm.chat([{"role": "user", "content": "hi"}], settings=settings())

    assert reply.text == "Hello."
    assert sent["model"] == "llama3:latest"


def test_the_choice_is_made_once_rather_than_per_turn(workspace, monkeypatch):
    """`active_model` is called on every chat and every stream. Asking Ollama
    each time would add a round trip to every turn for an answer that changes
    when somebody pulls a model."""
    calls = []

    def counted(_settings):
        calls.append(1)
        return ["llama3:latest"]

    monkeypatch.setattr(llm, "_installed_models", counted)

    for _ in range(5):
        llm.active_model(settings())

    assert len(calls) == 1


def test_pulling_the_configured_model_is_picked_up_without_a_restart(
    workspace, config_file, monkeypatch
):
    """health() refreshes the choice, and the events stream calls it every ten
    seconds -- so following the instruction the app just gave you takes effect
    on its own, which is the difference between an instruction and a chore."""
    brain = configure_model(config_file, "qwen2.5")
    installed = ["llama3:latest"]
    monkeypatch.setattr(llm, "_installed_models", lambda s: list(installed))

    assert llm.active_model(brain) == "llama3:latest"

    installed.append("qwen2.5:latest")
    llm.health()

    assert llm.active_model(brain) == "qwen2.5"


# -- and that it is said out loud ----------------------------------------


def test_health_is_ok_and_says_what_it_substituted(workspace, config_file, monkeypatch):
    configure_model(config_file, "qwen2.5")
    monkeypatch.setattr(llm, "_installed_models", lambda s: ["llama3:latest"])

    health = llm.health()

    # It works, so this is not a failure...
    assert health["ok"] is True
    # ...but it is not the model that was asked for, and it says so.
    assert health["substituted"] is True
    assert health["model"] == "llama3:latest"
    assert health["configured"] == "qwen2.5"
    assert health["model_installed"] is False
    assert "qwen2.5" in (health["error"] or "")
    assert "llama3:latest" in (health["error"] or "")


def test_health_says_nothing_when_the_configured_model_is_there(
    workspace, config_file, monkeypatch
):
    configure_model(config_file, "qwen2.5")
    monkeypatch.setattr(llm, "_installed_models", lambda s: ["qwen2.5:latest"])

    health = llm.health()

    assert health["ok"] is True
    assert health["substituted"] is False
    assert health["error"] is None


def test_health_is_not_ok_with_nothing_installed(workspace, monkeypatch):
    monkeypatch.setattr(llm, "_installed_models", lambda s: [])

    health = llm.health()

    assert health["ok"] is False
    assert health["substituted"] is False
    assert "not installed" in (health["error"] or "")


def test_an_unreachable_ollama_still_reports_itself(workspace, monkeypatch):
    monkeypatch.setattr(llm, "_installed_models", lambda s: None)

    health = llm.health()

    assert health["ok"] is False
    assert health["substituted"] is False
    assert "Ollama" in (health["error"] or "")


def test_the_event_stream_carries_the_substitution(workspace, config_file, monkeypatch):
    """`ok` alone cannot distinguish "working" from "working, with a model you
    did not choose", and the window needs to tell those apart."""
    from app import events

    configure_model(config_file, "qwen2.5")
    monkeypatch.setattr(llm, "_installed_models", lambda s: ["llama3:latest"])

    sample = events.brain_health()

    assert sample["ok"] is True
    assert sample["substituted"] is True
    assert sample["model"] == "llama3:latest"
    assert sample["configured"] == "qwen2.5"
