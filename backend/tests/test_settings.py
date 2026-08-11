"""Configuration loading — REQ-27, REQ-31.

The loopback rewrite has its own test because the bug it fixes is invisible.
Nothing failed: the assistant answered every message correctly, and simply took
four seconds longer than it needed to, on every turn, forever. A regression here
would look exactly like "the model is a bit slow today".
"""

from __future__ import annotations

import pytest
import yaml

from app.settings import BrainSettings, _prefer_ipv4, load_config, reset_config_cache


# -- the loopback rewrite -------------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        ("http://localhost:11434", "http://127.0.0.1:11434"),
        ("http://localhost", "http://127.0.0.1"),
        ("https://localhost:11434/v1", "https://127.0.0.1:11434/v1"),
        # Already correct, and the common non-loopback cases.
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
        ("http://192.168.1.40:11434", "http://192.168.1.40:11434"),
    ],
)
def test_loopback_is_pinned_to_ipv4(given, expected):
    assert _prefer_ipv4(given) == expected


def test_a_real_host_is_never_redirected():
    """Only the bare loopback name is ours to reinterpret.

    A host that merely contains the word must be left alone -- rewriting it
    would point the assistant at this machine instead of the one asked for,
    which is a far worse failure than being slow.
    """
    for host in (
        "http://localhost.example.com:11434",
        "http://my-localhost:11434",
        "http://ollama.internal:11434",
    ):
        assert _prefer_ipv4(host) == host


def test_the_default_does_not_resolve_through_ipv6():
    """Windows resolves localhost to ::1 first and Ollama binds IPv4 only."""
    assert "localhost" not in BrainSettings.ollama_host


def test_an_existing_config_saying_localhost_is_corrected_on_read(workspace, config_file):
    """Every config written before this was found says localhost.

    Fixing the default alone would leave installed machines paying the cost, so
    the correction has to happen as the file is read.
    """
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    raw["brain"] = {"provider": "ollama", "model": "llama3", "ollama_host": "http://localhost:11434"}
    config_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    reset_config_cache()

    assert load_config().brain.ollama_host == "http://127.0.0.1:11434"


def test_a_deliberate_remote_host_survives_loading(workspace, config_file):
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    raw["brain"] = {"provider": "ollama", "model": "llama3", "ollama_host": "http://10.0.0.5:11434"}
    config_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    reset_config_cache()

    assert load_config().brain.ollama_host == "http://10.0.0.5:11434"
