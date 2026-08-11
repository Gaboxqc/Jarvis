"""The routing prompt's size — REQ-27, REQ-33.

This file exists because of a failure that produced no error at all.

The tool catalog was rendered as indented JSON. At 48 skills that is ~5,600
tokens, against the 4,096-token context Ollama uses by default. Ollama does not
reject an over-long prompt; it drops the overflow and answers anyway. So the
router was choosing skills from a tool list with its middle missing, and the
only symptom was that it sometimes picked the wrong one — which we treated as
the model being small, and 'fixed' by adding more worked examples, which made
the prompt longer, which truncated more of the catalog.

The budget below is the guard. Adding a skill costs a line, and the assertion
fails long before the prompt reaches the context window again.
"""

from __future__ import annotations

from app.brain import llm, prompts
from app.settings import BrainSettings
from app.skills.base import Skill, SkillParam
from app.skills.registry import catalog, load_skills


def make_catalog(count: int) -> list[dict]:
    entries = []
    for i in range(count):
        skill = Skill()
        skill.name = f"demo.skill_{i}"
        skill.description = (
            "Does the demonstration thing. This second sentence is guidance for "
            "whoever maintains the code and has no business in the prompt."
        )
        skill.parameters = (
            SkillParam("query", "string", "What to look for.", required=True),
            SkillParam("limit", "integer", "How many.", required=False),
        )
        entries.append(skill.to_catalog_entry())
    return entries


# -- shape ----------------------------------------------------------------


def test_a_tool_is_one_line():
    line = prompts.tool_lines(make_catalog(1))
    assert "\n" not in line
    assert line.startswith("demo.skill_0(query, limit?)")


def test_optional_arguments_are_marked():
    line = prompts.tool_lines(make_catalog(1))
    assert "query," in line and "limit?" in line


def test_only_the_first_sentence_survives():
    """The rest is written for maintainers, and the router does not read it."""
    line = prompts.tool_lines(make_catalog(1))
    assert "Does the demonstration thing." in line
    assert "maintains the code" not in line


def test_enums_are_kept():
    """Dropping these would be a correctness loss, not a saving.

    'mark it read' has to become action=read, and the model cannot guess the
    spelling of a value it was never shown.
    """
    skill = Skill()
    skill.name = "mail.mark"
    skill.description = "Mark messages."
    skill.parameters = (
        SkillParam("action", "string", "What to do.", required=True,
                   enum=("read", "unread", "flag")),
    )
    assert "action=read|unread|flag" in prompts.tool_lines([skill.to_catalog_entry()])


# -- the budget -----------------------------------------------------------


def test_the_real_prompt_fits_the_context_window():
    """The regression guard. This is the assertion that would have caught it."""
    load_skills()
    prompt = prompts.router_prompt(catalog())
    estimate = llm.estimate_tokens([{"role": "system", "content": prompt}])

    budget = int(BrainSettings.context_tokens * 0.75)
    assert estimate < budget, (
        f"the routing prompt is ~{estimate} tokens against a "
        f"{BrainSettings.context_tokens}-token context. Ollama will truncate it "
        f"silently and route from a partial tool list."
    )


def test_the_compact_form_is_much_smaller_than_json():
    """Pins the reason for the format, so nobody 'tidies' it back to JSON."""
    import json

    entries = make_catalog(48)
    compact = len(prompts.tool_lines(entries))
    verbose = len(json.dumps(entries, indent=2))

    assert compact < verbose / 3


def test_every_skill_still_names_itself_and_its_required_args():
    """Compactness must not cost the router information it needs to call anything."""
    load_skills()
    rendered = prompts.tool_lines(catalog())

    for entry in catalog():
        assert entry["name"] in rendered
        for name, spec in (entry["parameters"] or {}).items():
            if spec.get("required"):
                assert name in rendered, f"{entry['name']} lost required arg {name}"


# -- the oversize warning -------------------------------------------------


def test_an_oversized_prompt_is_reported(caplog):
    settings = BrainSettings(context_tokens=1024)
    huge = [{"role": "system", "content": "x" * 40_000}]

    with caplog.at_level("WARNING"):
        llm._warn_if_oversized(huge, settings)

    assert "truncate" in caplog.text


def test_a_normal_prompt_says_nothing(caplog):
    settings = BrainSettings(context_tokens=8192)
    with caplog.at_level("WARNING"):
        llm._warn_if_oversized([{"role": "user", "content": "what time is it?"}], settings)
    assert caplog.text == ""
