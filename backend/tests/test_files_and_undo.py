"""File skills and undo — REQ-20, REQ-21, REQ-25, REQ-26."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.actions import gate, journal, undo
from app.skills.base import SkillContext, SkillError
from app.skills.system import paths
from app.skills.system.files import OrganizeFolderSkill

from .conftest import make_files


def test_organize_previews_real_counts_before_touching_anything(workspace):
    make_files(workspace, ["a.pdf", "b.pdf", "photo.jpg", "song.mp3"])

    outcome = gate.submit("system.organize_folder", {"folder": str(workspace)}, SkillContext())

    assert outcome.status == gate.NEEDS_CONFIRMATION
    assert "4 files" in outcome.preview
    assert "Documents" in outcome.preview and "Images" in outcome.preview
    # Preview must not move anything.
    assert (workspace / "a.pdf").exists()
    assert not (workspace / "Documents").exists()


def test_organize_then_undo_restores_every_file(workspace):
    names = ["a.pdf", "b.docx", "photo.jpg", "clip.mp4", "notes.txt", "archive.zip"]
    make_files(workspace, names)
    before = sorted(p.name for p in workspace.iterdir())

    parked = gate.submit("system.organize_folder", {"folder": str(workspace)}, SkillContext())
    executed = gate.confirm(parked.action_id, SkillContext())
    assert executed.status == gate.EXECUTED

    # Files moved into buckets.
    assert (workspace / "Documents" / "a.pdf").exists()
    assert (workspace / "Images" / "photo.jpg").exists()
    assert not (workspace / "a.pdf").exists()

    result = undo.undo_last()

    assert result.ok, result.message
    assert sorted(p.name for p in workspace.iterdir() if p.is_file()) == before
    # Empty bucket folders created by the run are cleaned up.
    assert not (workspace / "Documents").exists()


def test_one_organize_run_undoes_as_a_single_batch(workspace):
    make_files(workspace, ["a.pdf", "b.pdf", "c.jpg"])
    parked = gate.submit("system.organize_folder", {"folder": str(workspace)}, SkillContext())
    gate.confirm(parked.action_id, SkillContext())

    record = journal.get(parked.action_id)
    assert len(record.undo_payload["moves"]) == 3

    result = undo.undo_batch(record.batch_id)
    assert result.ok
    assert len(result.undone) == 1  # one operation reported, not three


def test_organize_never_overwrites_an_existing_file(workspace):
    (workspace / "Documents").mkdir()
    (workspace / "Documents" / "report.pdf").write_text("the original", encoding="utf-8")
    (workspace / "report.pdf").write_text("the newcomer", encoding="utf-8")

    parked = gate.submit("system.organize_folder", {"folder": str(workspace)}, SkillContext())
    gate.confirm(parked.action_id, SkillContext())

    assert (workspace / "Documents" / "report.pdf").read_text(encoding="utf-8") == "the original"
    assert (workspace / "Documents" / "report (1).pdf").read_text(encoding="utf-8") == "the newcomer"


def test_organize_ignores_subfolders_and_dotfiles(workspace):
    make_files(workspace, ["visible.pdf", ".hidden"])
    nested = workspace / "existing"
    nested.mkdir()
    make_files(nested, ["deep.pdf"])

    parked = gate.submit("system.organize_folder", {"folder": str(workspace)}, SkillContext())
    gate.confirm(parked.action_id, SkillContext())

    assert (workspace / ".hidden").exists()
    assert (nested / "deep.pdf").exists()
    assert (workspace / "Documents" / "visible.pdf").exists()


def test_paths_outside_the_allowed_roots_are_refused(workspace, tmp_path):
    outside = tmp_path / "not_allowed"
    outside.mkdir()
    make_files(outside, ["secret.pdf"])

    outcome = gate.submit("system.organize_folder", {"folder": str(outside)}, SkillContext())

    assert outcome.status == gate.FAILED
    assert "outside the folders" in (outcome.error or "")
    assert (outside / "secret.pdf").exists()


def test_traversal_out_of_an_allowed_root_is_refused(workspace):
    with pytest.raises(SkillError, match="outside the folders"):
        paths.resolve_allowed(str(workspace / ".." / ".." / "Windows"))


def test_resolve_allowed_accepts_a_path_inside_the_root(workspace):
    nested = workspace / "inner"
    nested.mkdir()
    assert paths.resolve_allowed(str(nested)) == nested.resolve()


def test_undo_refuses_an_irreversible_action_rather_than_pretending(workspace):
    from app.skills.base import Skill, SkillResult
    from app.skills import registry

    class Permanent(Skill):
        name = "test.permanent"
        description = "A skill that cannot be taken back."
        consequential = True
        reversible = False

        def preview(self, args):
            return "Do something permanent"

        def run(self, args, ctx):
            return SkillResult(ok=True, message="done permanently")

    registry.load_skills()[Permanent.name] = Permanent()

    parked = gate.submit("test.permanent", {}, SkillContext())
    assert parked.reversible is False
    executed = gate.confirm(parked.action_id, SkillContext())
    assert executed.status == gate.EXECUTED

    result = undo.undo_action(executed.action_id)
    assert not result.ok
    assert "not reversible" in result.message


def test_find_files_returns_distinguishing_detail(workspace):
    make_files(workspace, ["budget_2025.xlsx", "budget_2026.xlsx"])

    outcome = gate.submit("system.find_files", {"query": "budget"}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert len(outcome.result.data["matches"]) == 2
    # REQ-20: enough detail to tell them apart, and no file is opened.
    assert "modified" in outcome.message
    assert str(workspace) in outcome.message


def test_find_files_reports_no_matches_honestly(workspace):
    make_files(workspace, ["a.txt"])
    outcome = gate.submit("system.find_files", {"query": "nonexistent"}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert outcome.result.data["matches"] == []
    assert "No files matching" in outcome.message


def test_a_bare_folder_name_resolves_against_the_allowed_roots(workspace):
    """Found through the UI: the model says "Downloads", not an absolute path.

    Resolving that against the process working directory produced
    <install dir>/Downloads and a confusing refusal.
    """
    resolved = paths.resolve_allowed(workspace.name)

    assert resolved == workspace.resolve()


def test_a_bare_name_still_cannot_escape_the_allowed_roots(workspace):
    """Widening what can be *named* must not widen what can be *reached*."""
    with pytest.raises(SkillError, match="outside the folders"):
        paths.resolve_allowed("Windows")


def test_a_relative_subfolder_of_an_allowed_root_resolves(workspace):
    nested = workspace / "invoices"
    nested.mkdir()

    assert paths.resolve_allowed("invoices") == nested.resolve()


def test_an_unknown_bare_name_is_refused(workspace):
    with pytest.raises(SkillError):
        paths.resolve_allowed("no-such-folder-anywhere")
