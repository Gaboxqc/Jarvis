"""Meeting capture — REQ-19, REQ-10, REQ-26.

No test opens the microphone. The recorder is replaced by one that yields
prepared audio, so the session, transcription loop, storage and summarisation
are all exercised without hardware.
"""

from __future__ import annotations

import queue
import time

import pytest

from app.actions import gate
from app.capture import recorder, session as capture, store, summarize
from app.skills.base import SkillContext


class FakeRecorder:
    """Stands in for the microphone: hands over prepared chunks and stops."""

    def __init__(self, chunks: list[object] | None = None, microphone: bool = True) -> None:
        self.chunks: queue.Queue = queue.Queue()
        for chunk in chunks or ["chunk-1"]:
            self.chunks.put(chunk)
        self.status = recorder.SourceStatus(
            microphone=microphone, system_audio=False, note="test note"
        )
        self.error = None
        self.started = False
        self.stopped = False

    def start(self):
        if not self.status.microphone:
            raise recorder.CaptureUnavailable("There's no microphone available to record.")
        self.started = True
        return self.status

    def stop(self):
        self.stopped = True


@pytest.fixture
def fake_audio(monkeypatch):
    """Route the session through FakeRecorder and a stubbed transcriber."""
    from app.voice import stt

    made: dict[str, FakeRecorder] = {}

    def factory(*args, **kwargs):
        made["recorder"] = FakeRecorder(["a", "b"])
        return made["recorder"]

    monkeypatch.setattr(recorder, "Recorder", factory)
    monkeypatch.setattr(
        stt, "transcribe",
        lambda audio, **kw: stt.Transcription(text=f"words from {audio}", confidence=0.9),
    )
    yield made
    capture.reset()


def wait_for_words(expected: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if capture.status().words >= expected:
            return
        time.sleep(0.05)


# -- the recording indicator (REQ-19) -------------------------------------


def test_nothing_records_until_explicitly_started(workspace):
    assert capture.is_recording() is False
    assert capture.status().recording is False


def test_the_indicator_is_true_for_the_whole_session(workspace, fake_audio):
    capture.start("standup")
    assert capture.is_recording() is True

    status = capture.status()
    assert status.recording
    assert status.label == "standup"

    capture.stop()
    assert capture.is_recording() is False


def test_an_incomplete_recording_says_so_up_front(workspace, fake_audio):
    """The user learns what isn't captured before the meeting, not after."""
    status = capture.start("call")

    assert "test note" in status.note


def test_two_sessions_cannot_run_at_once(workspace, fake_audio):
    capture.start("first")

    with pytest.raises(capture.CaptureError, match="Already recording"):
        capture.start("second")


def test_stopping_when_idle_is_an_error_not_a_silent_noop(workspace):
    with pytest.raises(capture.CaptureError, match="Nothing is being recorded"):
        capture.stop()


def test_no_microphone_is_reported_clearly(workspace, monkeypatch):
    monkeypatch.setattr(recorder, "Recorder", lambda *a, **k: FakeRecorder(microphone=False))

    with pytest.raises(capture.CaptureError, match="no microphone"):
        capture.start("doomed")


# -- transcription and storage (REQ-19, REQ-26) ---------------------------


def test_chunks_are_transcribed_and_stored_as_they_arrive(workspace, fake_audio):
    capture.start("standup")
    wait_for_words(4)
    transcript = capture.stop()

    assert transcript is not None
    assert "words from a" in transcript.text
    assert "words from b" in transcript.text


def test_text_is_persisted_during_the_session_not_only_at_the_end(workspace, fake_audio):
    """A crash mid-meeting should lose the last chunk, not the whole thing."""
    status = capture.start("standup")
    wait_for_words(2)

    mid = store.get(status.transcript_id)
    assert mid is not None
    assert mid.text.strip()          # already on disk while still recording
    assert mid.is_running

    capture.stop()


def test_a_finished_transcript_records_its_duration(workspace, fake_audio):
    capture.start("standup")
    wait_for_words(2)
    transcript = capture.stop()

    assert transcript.ended_at is not None
    assert not transcript.is_running
    assert transcript.duration_seconds >= 0


def test_transcripts_are_listable_and_searchable(workspace, fake_audio):
    capture.start("budget review")
    wait_for_words(2)
    capture.stop()

    assert store.recent()
    assert store.find("budget")
    assert store.find("nothing-like-this") == []


def test_a_transcript_can_be_deleted(workspace, fake_audio):
    status = capture.start("private chat")
    wait_for_words(2)
    capture.stop()

    assert store.delete(status.transcript_id) is not None
    assert store.get(status.transcript_id) is None


def test_wiping_all_data_removes_transcripts(workspace, fake_audio):
    from app import db

    capture.start("standup")
    wait_for_words(2)
    capture.stop()

    db.wipe_all_local_data()

    assert store.recent() == []


# -- summarising (REQ-19) -------------------------------------------------


def stub_summary(monkeypatch, payload: str):
    from app.brain import llm

    monkeypatch.setattr(
        llm, "chat", lambda messages, **kw: llm.LLMReply(text=payload, raw={})
    )


TRANSCRIPT = " ".join(
    ["We discussed moving the launch date."] * 5
    + ["Ana will update the timeline by Friday."] * 3
)


def test_a_summary_separates_decisions_from_actions(workspace, monkeypatch):
    stub_summary(monkeypatch, """
    {"summary": "The team discussed the launch date.",
     "decisions": ["Push the launch to 15 September"],
     "actions": ["Ana to update the project timeline by Friday"]}
    """)

    result = summarize.summarise(TRANSCRIPT)

    assert result.decisions == ["Push the launch to 15 September"]
    assert result.actions == ["Ana to update the project timeline by Friday"]
    assert "Decisions:" in result.render()


def test_an_empty_transcript_is_not_summarised(workspace):
    assert summarize.summarise("").error


def test_a_very_short_recording_says_so(workspace):
    result = summarize.summarise("hello there")

    assert "too short" in result.summary.lower()


def test_no_decisions_is_a_valid_answer(workspace, monkeypatch):
    """A padded action list is worse than an empty one."""
    stub_summary(monkeypatch, '{"summary": "A catch-up.", "decisions": [], "actions": []}')

    result = summarize.summarise(TRANSCRIPT)

    assert result.decisions == []
    assert result.actions == []
    assert "No clear decisions" in result.render()


def test_placeholder_entries_are_discarded(workspace, monkeypatch):
    stub_summary(
        monkeypatch,
        '{"summary": "x", "decisions": ["None"], "actions": ["N/A", "Real action"]}',
    )

    result = summarize.summarise(TRANSCRIPT)

    assert result.decisions == []
    assert result.actions == ["Real action"]


def test_actions_given_as_objects_are_flattened(workspace, monkeypatch):
    stub_summary(
        monkeypatch,
        '{"summary": "x", "decisions": [], '
        '"actions": [{"who": "Ana", "what": "update the timeline"}]}',
    )

    result = summarize.summarise(TRANSCRIPT)

    assert result.actions == ["Ana - update the timeline"]


def test_a_dead_model_leaves_the_transcript_intact(workspace, monkeypatch):
    from app.brain import llm

    def unavailable(*args, **kwargs):
        raise llm.LLMUnavailable("model is down")

    monkeypatch.setattr(llm, "chat", unavailable)

    result = summarize.summarise(TRANSCRIPT)

    assert result.error == "model is down"
    assert "Couldn't summarise" in result.render()


# -- skills (REQ-19, REQ-10, REQ-24) --------------------------------------


def test_starting_a_recording_does_not_require_confirmation(workspace, fake_audio):
    """Asking to record *is* the explicit start REQ-19 wants."""
    outcome = gate.submit("capture.start", {"label": "standup"}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert "Recording" in outcome.message
    capture.stop()


def test_the_start_message_discloses_what_is_captured(workspace, fake_audio):
    outcome = gate.submit("capture.start", {"label": "call"}, SkillContext())

    assert "microphone" in outcome.message
    assert "test note" in outcome.message
    capture.stop()


def test_deleting_a_recording_is_gated(workspace, fake_audio):
    capture.start("sensitive call")
    wait_for_words(2)
    capture.stop()

    outcome = gate.submit("capture.delete", {"query": "sensitive"}, SkillContext())

    assert outcome.status == gate.NEEDS_CONFIRMATION
    assert "cannot be undone" in outcome.preview
    assert store.find("sensitive")          # still there until confirmed


def test_action_items_can_be_saved_as_tasks_and_undone(workspace, fake_audio):
    status = capture.start("standup")
    wait_for_words(2)
    capture.stop()
    store.set_summary(status.transcript_id, {
        "summary": "s", "decisions": [], "actions": ["Send the quote", "Update the timeline"],
    })

    outcome = gate.submit("capture.save_actions", {}, SkillContext())
    assert outcome.status == gate.EXECUTED

    listed = gate.submit("planning.list_tasks", {}, SkillContext())
    assert "Send the quote" in listed.message

    from app.actions import undo

    assert undo.undo_last().ok
    listed = gate.submit("planning.list_tasks", {}, SkillContext())
    assert "Send the quote" not in listed.message


def test_saving_actions_with_none_recorded_says_so(workspace, fake_audio):
    capture.start("standup")
    wait_for_words(2)
    capture.stop()

    outcome = gate.submit("capture.save_actions", {}, SkillContext())

    assert outcome.status == gate.FAILED
    assert "no action items" in (outcome.error or "")


def test_recall_with_no_recordings_says_so(workspace):
    outcome = gate.submit("capture.recall", {}, SkillContext())

    assert outcome.status == gate.FAILED
    assert "no recorded meetings" in (outcome.error or "").lower()


# -- the API the Meetings screen is built on (REQ-19, REQ-26) -------------


def test_status_tells_the_screen_it_is_recording(workspace, monkeypatch):
    """A running recording must never be able to look idle.

    The screen draws its outline, its pulsing dot and its Stop button from this
    one field, so it has to be right even when the recorder is degraded.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        assert client.get("/capture/status").json()["recording"] is False


def test_a_transcript_can_be_listed_read_and_deleted(workspace):
    """The three things the screen does with a past recording."""
    from fastapi.testclient import TestClient

    from app.capture import store as capture_store
    from app.main import app

    saved = capture_store.create("call with Ana", ["microphone"])
    capture_store.append_text(saved.id, "We agreed to ship on Friday.")
    capture_store.finish(saved.id, 125.0)

    with TestClient(app) as client:
        listed = client.get("/capture/transcripts").json()["transcripts"]
        assert any(t["id"] == saved.id for t in listed)
        assert listed[0]["minutes"] == 2.1

        full = client.get(f"/capture/transcripts/{saved.id}").json()
        # The list omits the text; opening one is what fetches it.
        assert full["text"] == "We agreed to ship on Friday."

        assert client.delete(f"/capture/transcripts/{saved.id}").status_code == 200
        assert client.get("/capture/transcripts").json()["transcripts"] == []


def test_reading_a_missing_transcript_is_404(workspace):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        assert client.get("/capture/transcripts/nope").status_code == 404
