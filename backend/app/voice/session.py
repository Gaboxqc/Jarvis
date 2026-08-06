"""The voice turn — REQ-1, REQ-2, REQ-3, REQ-4.

Ties capture, transcription, the orchestrator and speech into one round trip,
and keeps voice a *transport* rather than a second brain: the same
`orchestrator.handle_turn` runs, so voice and text have identical capability
(REQ-1) and the Action Gate is in front of exactly the same actions.

Confirmations are the one place voice needs care. A parked action is spoken
aloud and the following utterance answers it — but only ever by handing the
action id back, exactly as the CLI does. "Yes" heard by a microphone is not
special, and a mis-transcription must not be able to approve something.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..brain import orchestrator
from ..settings import load_config
from . import audio, stt, tts

log = logging.getLogger(__name__)

SESSION_ID = "voice"


@dataclass
class VoiceTurn:
    heard: str = ""
    confidence: float = 0.0
    reply: str = ""
    spoke: bool = False
    pending_action_id: str | None = None
    error: str | None = None
    skill_calls: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "heard": self.heard,
            "confidence": round(self.confidence, 3),
            "reply": self.reply,
            "spoke": self.spoke,
            "needs_confirmation": self.pending_action_id is not None,
            "error": self.error,
        }


class VoiceSession:
    """One conversation over the microphone."""

    def __init__(self, on_state: Callable[[str], None] | None = None) -> None:
        self._on_state = on_state
        self._pending_action_id: str | None = None

    def _state(self, value: str) -> None:
        if self._on_state:
            try:
                self._on_state(value)
            except Exception:  # noqa: BLE001
                log.debug("state callback failed", exc_info=True)

    # -- one turn ---------------------------------------------------------

    def listen_once(self) -> VoiceTurn:
        """Capture one utterance and run it as a turn."""
        config = load_config().voice

        if not config.input_enabled:
            return VoiceTurn(error="Voice input is turned off.")

        try:
            capture = audio.record_until_silence(on_state=self._state)
        except audio.AudioUnavailable as exc:
            return VoiceTurn(error=str(exc))

        if capture.is_empty:
            self._state("idle")
            return VoiceTurn(error="I didn't hear anything.")

        self._state("thinking")
        try:
            heard = stt.transcribe(capture.samples)
        except stt.STTUnavailable as exc:
            self._state("idle")
            return VoiceTurn(error=str(exc))

        return self.handle_transcription(heard)

    def handle_transcription(self, heard: stt.Transcription) -> VoiceTurn:
        """Run a transcription through the brain, guarding low confidence."""
        config = load_config().voice
        turn = VoiceTurn(heard=heard.text, confidence=heard.confidence)

        if heard.is_empty or heard.no_speech:
            turn.error = "I didn't catch any words there."
            self._say_and_finish(turn, "I didn't catch that.")
            return turn

        # REQ-3: ask rather than guess. Whisper turns a cough into a plausible
        # sentence, and a plausible sentence reaches the router looking like an
        # instruction — which is precisely what must not happen.
        if not heard.is_confident(config.min_confidence):
            turn.error = "low_confidence"
            self._say_and_finish(
                turn, f"I think you said \"{heard.text}\", but I'm not sure. Could you repeat that?"
            )
            return turn

        result = orchestrator.handle_turn(
            heard.text, SESSION_ID, pending_action_id=self._pending_action_id
        )

        turn.reply = result.reply
        turn.skill_calls = result.skill_calls
        turn.error = result.error

        # Carry the parked action forward so the next utterance can answer it —
        # by id, never by the word alone.
        self._pending_action_id = result.pending.action_id if result.pending else None
        turn.pending_action_id = self._pending_action_id

        self._say_and_finish(turn, result.reply)
        return turn

    def _say_and_finish(self, turn: VoiceTurn, text: str) -> None:
        if not text:
            self._state("idle")
            return
        self._state("speaking")
        try:
            spoken = tts.speak(text)
            turn.spoke = spoken is not None
        except tts.TTSUnavailable as exc:
            # The turn already happened and the reply is on screen; losing the
            # audio is a degradation, not a failure (REQ-27).
            log.warning("speech output failed: %s", exc)
            turn.spoke = False
        finally:
            self._state("idle")

    # -- state ------------------------------------------------------------

    @property
    def pending_action_id(self) -> str | None:
        return self._pending_action_id

    def clear_pending(self) -> None:
        self._pending_action_id = None


def status() -> dict[str, Any]:
    """Everything the UI needs to explain why voice is or isn't working."""
    from . import models, wake

    config = load_config().voice
    return {
        "enabled": config.enabled,
        "input_enabled": config.input_enabled,
        "output_enabled": config.output_enabled,
        "microphone": audio.has_microphone(),
        "stt": stt.status(),
        "tts": tts.status(),
        "wake": wake.status(),
        "models_ready": not models.missing(),
        "download_mb": models.total_download_mb(),
    }
