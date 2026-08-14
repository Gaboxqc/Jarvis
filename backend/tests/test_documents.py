"""Document indexing, retrieval and citations — REQ-16, REQ-20, REQ-31."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.actions import gate
from app.index import scanner, store
from app.index.chunk import chunk_sections
from app.index.extract import ExtractionError, Section, extract
from app.skills.base import SkillContext

LEASE = """# Tenancy agreement

## Deposit
The tenant shall pay a security deposit of 1,850 euros before occupation.
The deposit is refundable within thirty days of the end of the tenancy.

## Termination
Either party may terminate this agreement by giving two months written notice.
"""


def write(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


# -- extraction -----------------------------------------------------------


def test_markdown_headings_become_section_labels(docs_folder):
    path = write(docs_folder, "lease.md", LEASE)

    sections = extract(path)

    labels = [s.label for s in sections]
    assert "Deposit" in labels
    assert "Termination" in labels


def test_plain_text_is_extracted_as_one_section(docs_folder):
    path = write(docs_folder, "notes.txt", "Remember to cancel the gym membership.")

    sections = extract(path)

    assert len(sections) == 1
    assert "cancel the gym" in sections[0].text


def test_an_empty_file_is_reported_not_silently_indexed(docs_folder):
    path = write(docs_folder, "blank.txt", "   \n\n  ")

    with pytest.raises(ExtractionError):
        extract(path)


def test_an_unreadable_file_is_recorded_so_the_user_can_be_told(docs_folder):
    """A scanned PDF indexes nothing; saying so beats silently searching air."""
    write(docs_folder, "scan.pdf", "not really a pdf")

    result = scanner.scan(force=True)

    assert result.failed == 1
    problems = store.failures()
    assert len(problems) == 1
    assert problems[0]["file"] == "scan.pdf"
    assert problems[0]["error"]


# -- chunking -------------------------------------------------------------


def test_long_text_is_split_into_several_chunks():
    body = " ".join(f"Sentence number {i} about tenancy law." for i in range(400))
    chunks = chunk_sections([Section("Body", body)])

    assert len(chunks) > 1
    assert all(c.section == "Body" for c in chunks)
    assert all(len(c.text) <= 1400 for c in chunks)


def test_short_sections_stay_whole():
    chunks = chunk_sections([Section("Deposit", "The deposit is 1,850 euros.")])

    assert len(chunks) == 1
    assert chunks[0].text == "The deposit is 1,850 euros."


def test_chunks_carry_their_section_label_for_citation():
    chunks = chunk_sections([Section("page 12", "Text on page twelve.")])
    assert chunks[0].section == "page 12"


# -- scanning -------------------------------------------------------------


def test_scan_indexes_configured_folders(docs_folder):
    write(docs_folder, "lease.md", LEASE)
    write(docs_folder, "notes.txt", "The car insurance renews in March.")

    result = scanner.scan(force=True)

    assert result.indexed == 2
    assert store.stats()["documents"] == 2


def test_a_second_scan_does_no_work_when_nothing_changed(docs_folder):
    write(docs_folder, "lease.md", LEASE)
    scanner.scan(force=True)

    result = scanner.scan(force=True)

    assert result.changed == 0
    assert "up to date" in result.summary()


def test_an_edited_file_is_reindexed(docs_folder):
    path = write(docs_folder, "notes.txt", "The old content mentions bicycles.")
    scanner.scan(force=True)
    assert store.search("bicycles")

    # A changed size is enough; mtime resolution varies by filesystem.
    path.write_text("The new content mentions helicopters instead.", encoding="utf-8")
    result = scanner.scan(force=True)

    assert result.updated == 1
    assert store.search("helicopters")
    assert not store.search("bicycles")


def test_a_deleted_file_is_dropped_from_the_index(docs_folder):
    path = write(docs_folder, "temp.txt", "Ephemeral content about kayaks.")
    scanner.scan(force=True)
    assert store.search("kayaks")

    path.unlink()
    result = scanner.scan(force=True)

    assert result.removed == 1
    assert store.search("kayaks") == []


def test_oversized_files_are_skipped(docs_folder):
    write(docs_folder, "huge.txt", "x " * 3_000_000)  # over the 5 MB test limit

    result = scanner.scan(force=True)

    assert result.skipped >= 1
    assert store.stats()["documents"] == 0


def test_scanning_backs_off_during_a_focus_session(docs_folder):
    """REQ-31 — indexing must not compete with a machine in use."""
    from app import focus

    write(docs_folder, "lease.md", LEASE)
    focus.start(25)

    result = scanner.scan()

    assert result.indexed == 0
    assert any("focus session" in e for e in result.errors)
    assert store.stats()["documents"] == 0


def test_an_explicit_reindex_overrides_the_back_off(docs_folder):
    """The user is right there waiting, so honour the request."""
    from app import focus

    write(docs_folder, "lease.md", LEASE)
    focus.start(25)

    outcome = gate.submit("documents.reindex", {}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert store.stats()["documents"] == 1


# -- retrieval and citations ----------------------------------------------


def test_search_finds_the_relevant_passage(docs_folder):
    write(docs_folder, "lease.md", LEASE)
    scanner.scan(force=True)

    hits = store.search("how much was the deposit")

    assert hits
    assert "1,850" in hits[0].text


def test_results_are_cited_with_file_and_section(docs_folder):
    write(docs_folder, "lease.md", LEASE)
    scanner.scan(force=True)

    outcome = gate.submit("documents.search", {"query": "security deposit"}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert "lease.md" in outcome.message
    assert "Deposit" in outcome.message
    assert outcome.result.data["sources"]


def test_no_match_says_so_rather_than_returning_something_irrelevant(docs_folder):
    write(docs_folder, "lease.md", LEASE)
    scanner.scan(force=True)

    outcome = gate.submit(
        "documents.search", {"query": "photosynthesis chlorophyll"}, SkillContext()
    )

    assert outcome.status == gate.EXECUTED
    assert outcome.result.data["results"] == []
    assert "Nothing in the indexed documents" in outcome.message


def test_searching_with_an_empty_index_explains_itself(docs_folder):
    outcome = gate.submit("documents.search", {"query": "anything"}, SkillContext())

    assert outcome.status == gate.FAILED
    assert "Nothing is indexed yet" in (outcome.error or "")


@pytest.mark.parametrize(
    "query",
    [
        "what's the deposit?",                 # apostrophe
        'the "security" deposit',              # embedded quotes
        "deposit AND termination OR notice",   # bare FTS operators
        "deposit NEAR/3 refund",               # NEAR syntax
        "deposit*",                            # wildcard
        "-deposit ^start :colon",              # column filter and negation syntax
        "((unbalanced",                        # unbalanced parens
        "   ",                                 # whitespace only
        "?!@#$%",                              # no usable terms at all
    ],
)
def test_fts_operators_in_a_question_never_raise(docs_folder, query):
    """FTS5 MATCH treats these as syntax; a question must not become an error."""
    write(docs_folder, "lease.md", LEASE)
    scanner.scan(force=True)

    hits = store.search(query)  # must not raise

    assert isinstance(hits, list)


def test_query_terms_are_rebuilt_not_escaped():
    terms = store.extract_terms('the "deposit" AND refund*')

    assert '"deposit"' in terms
    assert '"refund"' in terms
    # Operators and noise words never survive into the expression.
    assert not any("*" in t or "AND" in t for t in terms)


def test_index_can_be_cleared(docs_folder):
    write(docs_folder, "lease.md", LEASE)
    scanner.scan(force=True)
    assert store.stats()["documents"] == 1

    store.clear()

    assert store.stats()["documents"] == 0
    assert store.search("deposit") == []


def test_wiping_all_data_also_clears_the_document_index(docs_folder):
    from app import db

    write(docs_folder, "lease.md", LEASE)
    scanner.scan(force=True)

    db.wipe_all_local_data()

    assert store.stats()["documents"] == 0


# -- find_files by content (REQ-20) ---------------------------------------


def test_find_files_can_match_on_content(docs_folder, config_file):
    import yaml

    from app.settings import reset_config_cache

    write(docs_folder, "lease.md", LEASE)
    write(docs_folder, "unrelated.txt", "Nothing to do with property.")
    scanner.scan(force=True)

    # Let the file skills read the docs folder too.
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    raw["system"]["allowed_roots"] = [str(docs_folder)]
    config_file.write_text(yaml.safe_dump(raw), encoding="utf-8")
    reset_config_cache()

    outcome = gate.submit("system.find_files", {"contains": "security deposit"}, SkillContext())

    assert outcome.status == gate.EXECUTED
    names = [m["name"] for m in outcome.result.data["matches"]]
    assert names == ["lease.md"]


def test_find_files_by_content_says_when_nothing_is_indexed(docs_folder):
    outcome = gate.submit("system.find_files", {"contains": "quantum"}, SkillContext())

    assert outcome.status == gate.EXECUTED
    assert outcome.result.data["matches"] == []
    assert "indexed" in outcome.message


# -- PDF, the format this feature exists for ------------------------------


def test_a_real_pdf_is_extracted_with_page_numbers(docs_folder):
    from .conftest import minimal_pdf

    (docs_folder / "lease.pdf").write_bytes(
        minimal_pdf([
            ["TENANCY AGREEMENT", "",
             "The tenant shall pay a security deposit of 1850 euros",
             "before taking occupation of the property."],
            ["TERMINATION", "",
             "Either party may terminate by giving two months notice."],
        ])
    )

    sections = extract(docs_folder / "lease.pdf")

    assert [s.label for s in sections] == ["page 1", "page 2"]
    assert "1850 euros" in sections[0].text
    assert "two months notice" in sections[1].text


def test_a_pdf_answer_cites_its_page(docs_folder):
    from .conftest import minimal_pdf

    (docs_folder / "lease.pdf").write_bytes(
        minimal_pdf([
            ["Introduction and preamble to the agreement."],
            ["The security deposit of 1850 euros is refundable",
             "within thirty days of the end of the tenancy."],
        ])
    )
    scanner.scan(force=True)

    outcome = gate.submit("documents.search", {"query": "deposit refundable"}, SkillContext())

    assert outcome.status == gate.EXECUTED
    # The page number is what makes the citation actionable.
    assert "lease.pdf (page 2)" in outcome.message


# -- the API the Documents screen is built on (REQ-16) ---------------------


def test_search_results_carry_a_citation_and_the_passage(workspace, docs_folder):
    """An answer about your lease is worth nothing if you can't see the line.

    The screen quotes the passage verbatim and names the file, so both have to
    survive the trip through the API.
    """
    from fastapi.testclient import TestClient

    from app.index import scanner
    from app.main import app

    (docs_folder / "tenancy.txt").write_text(
        "Section 3 - Deposit\nThe tenant shall pay a security deposit of 1450 EUR.",
        encoding="utf-8",
    )
    scanner.scan(force=True)

    with TestClient(app) as client:
        results = client.get("/documents/search?q=security deposit").json()["results"]

    assert results, "the indexed passage should be findable"
    hit = results[0]
    assert hit["file"] == "tenancy.txt"
    assert "1450 EUR" in hit["text"]
    assert "tenancy.txt" in hit["citation"]
    assert hit["path"].endswith("tenancy.txt")


def test_status_tells_the_screen_why_a_search_would_find_nothing(workspace):
    """Empty results have several causes and they are not interchangeable.

    "Nothing matched", "nothing is indexed" and "the scan is waiting" mean
    different things, and a user who cannot tell them apart concludes the
    feature is broken. The screen needs all three from this one call.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        status = client.get("/documents/status").json()

    for key in ("folders", "running", "paused", "deferred_because", "documents", "chunks",
                "failed", "failures"):
        assert key in status, f"the screen needs {key} to explain an empty result"


def test_clearing_the_index_leaves_the_files_alone(workspace, docs_folder):
    """REQ-26: 'clear index' must never read as 'delete my documents'."""
    from fastapi.testclient import TestClient

    from app.index import scanner
    from app.main import app

    document = docs_folder / "keep-me.txt"
    document.write_text("something worth keeping", encoding="utf-8")
    scanner.scan(force=True)

    with TestClient(app) as client:
        assert client.delete("/documents/index").json()["cleared_documents"] >= 1
        assert client.get("/documents/status").json()["documents"] == 0

    assert document.exists(), "clearing the index must not touch the file"
    assert document.read_text(encoding="utf-8") == "something worth keeping"
