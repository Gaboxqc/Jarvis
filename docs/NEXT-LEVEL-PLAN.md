# Kai — architectural review and plan

Reviewed at `513a08c` (branch `fix/quarantine-only-real-corruption`), 2026-10-15.

Baseline verified before writing any of this:

```
ruff check .                  All checks passed
mypy backend/app              Success: no issues found in 87 source files
pytest (backend)              662 passed, 1 skipped, exit 0  (~9 min)
```

Nothing below is a response to a failing check. The project is green; this is
about where it is structurally, and what the next level costs.

---

## 1. Assessment

**Scale.** ~11k lines of backend Python across 87 modules, 7.4k lines of
TypeScript across 34 files, 270 lines of Rust, 42 test modules, 147 commits.
Three front ends (CLI, browser, Tauri window) over one domain layer.

**What is genuinely good, and should be protected as the project grows:**

- **The Action Gate is a real invariant, not a convention.** `gate.confirm()`
  takes an id and consumes it; `allow_pre_approval` exists so a standing
  approval cannot satisfy a per-message rule; severity is asked of the skill
  per call so the conversation cannot downgrade an action. The registry
  *enforces* at import time that `reversible` implies `undo()` and
  `consequential` implies `preview()` ([registry.py:91](../backend/app/skills/registry.py#L91)).
  Very few assistant codebases have an authorization story this tight.
- **One domain layer, three clients.** `main.py` holds no business logic, the
  CLI imports the same modules, and the UI cannot reach a capability the
  terminal lacks. That property is what makes the whole trust argument
  checkable rather than asserted.
- **Decisions are recorded where they are made.** `brain/llm.py` says native
  tool-calling lost a measurement 89/96 vs 95/96 and why it is kept anyway.
  `index/search.py` carries the table that justifies the resident vector cache.
  `db.py` explains why a `MigrationError` must not inherit from
  `sqlite3.DatabaseError`. This is the most valuable asset in the repo and the
  easiest to lose; keep doing it.
- **Migrations are correct**, including the thing most projects get wrong:
  `CREATE TABLE IF NOT EXISTS` for fresh installs *plus* a numbered migration
  path, with a test that catches a `SCHEMA_VERSION` bump without a migration.
- **The release pipeline refuses to ship broken artifacts** — `build.ps1` gates
  on the suite and the freeze self-test, `publish.py` refuses when the five
  version files disagree or a `.sig` is missing.

**Where it is structurally weak:**

| | |
|---|---|
| `main.py` is 1,413 lines, ~70 endpoints, 60+ function-local imports | one file owns the entire HTTP surface |
| `ui/src/api.ts` is 921 lines of hand-written types mirroring Python | the one seam where a backend change breaks the UI with both halves green |
| The turn loop's intent tables are English-first regexes | the ungrounded-answer guard does **not** fire for a Spanish persona |
| No per-session serialization of turns | two in-flight turns interleave into one conversation |
| 3 UI test files for 25 components | the half of the Action Gate the user actually clicks is barely covered |
| No coverage number, no perf regression test | the performance numbers in the comments are the spec and nothing pins them |
| Windows-bound in ~6 places, portable everywhere else | the product ceiling |

---

## 2. Corrections

Ordered by (severity × likelihood). Each is small and sits under existing tests.

**Phase A is done.** All ten are applied, each with the tests that would have
caught it. Branched from `main` rather than stacked on the open PR #34, which
touches `db.py` for an unrelated reason and shares no line with any of this.
On that base the suite goes from 648 tests to 697 — 696 passing, 1 skipped —
and `ruff`, `mypy`, `tsc --noEmit` and the UI suite are green.

Two notes on what the work taught us, recorded because they change the plan
rather than just completing it:

- **C1 took two passes.** The Spanish patterns written the obvious way refused
  every *offer* the assistant makes ("¿Quieres que guarde esto?") and every
  honest *denial* ("No he enviado nada"). For Spanish -ar verbs the
  first-person preterite and the present subjunctive differ by one accent, and
  negation sits differently than in English. Both are now excluded and pinned
  by tests. The lesson generalises to **R3**: a locale is not a translation of
  the patterns, it is its own set of false positives, and the structural fix
  should make room for per-locale negative cases.
- **C5 was half a misreading.** The two extra `all_facts()` calls I counted are
  on different entry points (`add()` and `relevant()`), not two reads in one
  call — so only the batched `last_used_at` write was real, and it is done.
  The genuine remaining item is `_by_meaning` reading the whole `fact_vectors`
  table per turn, which needs the `index_state` generation counter extended to
  that table and therefore a schema migration. It stays deferred, as planned.

### C1 — The ungrounded-answer guard is English-only ★ highest value — **done**

[`orchestrator.py:78-97`](../backend/app/brain/orchestrator.py#L78) —
`_EXTERNAL_DATA` and `_ACTION_CLAIM` contain no Spanish. The module's other
intent tables (`_AFFIRMATIVE`, `_UNDO`, `_PLEASANTRIES`) *were* translated, so
this reads as an oversight rather than a decision.

Consequence: with `persona.language: es`, `_guard_ungrounded_reply()` can never
fire. A Spanish user gets exactly the failure the guard was written to
prevent — "he añadido la reunión" when nothing ran — and REQ-27's "never claim
to have done something you have not done" is enforced for one of the two
shipped languages.

Fix: translate both patterns, and add the Spanish cases to
`test_orchestrator.py` beside the English ones. See **R3** for the structural
version of this fix.

### C2 — Upload is buffered before its size is checked — **done**

[`main.py:1116`](../backend/app/main.py#L1116) — `raw = await file.read()` then
`if len(raw) > 50 * 1024 * 1024`. The limit is enforced after the whole body
has been accepted. Starlette spools past a threshold so this is disk rather
than RAM, but it is unbounded disk on a local service that anything on the
machine can reach with the token.

Fix: reject on `Content-Length` first, then read in chunks against a running
total.

### C3 — No bounds on `limit` query parameters — **done**

[`main.py:332`](../backend/app/main.py#L332) `action_history(limit: int = 25)`,
[`main.py:655`](../backend/app/main.py#L655) `search_documents(q, limit: int = 5)`.
`Query(...)` appears zero times in the file. `limit=100000000` is a valid
request today.

Fix: `Annotated[int, Query(ge=1, le=100)]`. Cheap, and it makes the contract
visible in the OpenAPI schema that **R2** will consume.

### C4 — Two concurrent turns in one session interleave — **done**

There is no lock anywhere in `orchestrator.py` or `short_term.py`. Two
overlapping `POST /turn` calls with the same `session_id` both
`short_term.record()` the user text, both read a window that now contains the
other's message, and both can park a gated action. The UI serializes by
disabling the composer; nothing else does, and `/turn/stream` is long-lived.

Fix: a per-session `threading.Lock` registry in the orchestrator — second
caller either waits or gets a 409 that says a turn is already running. Prefer
409: a queued turn answers a question the user has already moved on from.

### C5 — `_by_meaning` reads the whole vector table every turn — **partly done**

[`long_term.py:171`](../backend/app/memory/long_term.py#L171) —
`SELECT fact_id, vector FROM fact_vectors WHERE model = ?` with no limit, once
per turn, plus `all_facts()` twice more in `_by_overlap` and `_find_duplicate`.

This is the exact pattern `index/search.py` already solved for `chunk_vectors`,
with a measured table showing 666ms at 20k rows. Facts are fewer, so this is
not urgent — but the mechanism (resident matrix + a generation counter bumped
by trigger) already exists and should be reused rather than re-derived later
under pressure.

Fix now (cheap): `relevant()` issues one `UPDATE … WHERE id IN (…)` instead of
N statements, and `_find_duplicate` reuses the facts `_by_overlap` already
loaded. Fix later: extend the `index_state` generation counter to
`fact_vectors` and share the resident-cache code.

### C6 — The scheduler thread does four unrelated jobs at one cadence — **done**

[`scheduler/service.py:tick()`](../backend/app/scheduler/service.py#L161) runs
reminder delivery, routine *execution*, index scanning and model unloading in
one 5-second loop. A routine step that opens IMAP has a 20-second timeout
([mail.py:32](../backend/app/connectors/mail.py#L32)); while it runs, every
reminder is late and the index does not advance. `_run_routines` catches
exceptions but not slowness.

Fix: dispatch routine execution to a short-lived worker thread and keep `tick()`
to bookkeeping. The delivery/advance split already guarantees nothing fires
twice, so this is safe.

### C7 — `MAX_SKILLS_PER_TURN` is a constant the prompt is told separately — **done**

[`orchestrator.py:39`](../backend/app/brain/orchestrator.py#L39) is a module
constant, passed into `prompts.router_prompt(available, MAX_SKILLS_PER_TURN)`.
The two cannot disagree *today* because one feeds the other — but the limit is
a product decision living in a module private, not in `Config`.

Fix: `ActionSettings.max_skills_per_turn`, defaulted to 3.

### C8 — The API layer calls a skill's private method — **done**

[`main.py:1233`](../backend/app/main.py#L1233) —
`StartFocusSkill._to_close()`. The comment explains why the endpoint wants the
same answer the skill computes, which is right; reaching through the underscore
to get it is not.

Fix: promote it to `focus.would_close()` and have both call that.

### C9 — Destructive notification drain blocks a second window

Documented honestly at [`events.py:22`](../backend/app/events.py#L22): two open
windows split notifications rather than both seeing them. It is a property
today and a bug the moment anything ships a second surface. Flagged here so it
is decided deliberately — see **F4**.

### C10 — `synchronous=NORMAL` is not set under WAL — **done**

[`db.py:453`](../backend/app/db.py#L451) sets `journal_mode=WAL` and
`foreign_keys=ON`. Under WAL, `synchronous=FULL` (the default) fsyncs every
commit, and `db.execute()` commits per statement. For a local single-user app
with a WAL, `NORMAL` is the standard setting and removes an fsync from every
reminder write, journal entry and conversation turn.

Fix: one `PRAGMA`, plus a sentence in `_prepare` saying what is traded (a
commit can be lost on OS crash; the database stays consistent).

---

## 3. Refactors

### R1 — Split `main.py` into routers ★ biggest structural win — **done**

1,413 lines and ~70 endpoints in one module, with ~60 function-local imports
that exist to keep the frozen build's import time down. Nobody can own this
file, and every new endpoint makes the next merge conflict more likely.

```
app/api/__init__.py      build_app(): middleware order + include_router
app/api/conversation.py  /turn, /turn/stream, /session
app/api/actions.py       /actions/*
app/api/memory.py        /memory
app/api/scheduling.py    /reminders, /routines, /shortcuts, /tasks
app/api/documents.py     /documents/*
app/api/connectors.py    /connectors/*, /briefing
app/api/voice.py         /voice/*, /voice/clone/*
app/api/capture.py       /capture/*
app/api/system.py        /focus, /state, /events, /health
app/api/privacy.py       /privacy/*, /settings, /diagnostics/*
```

`main.py` keeps only the lifespan, `LocalCredentials`, the CORS policy and the
long comment explaining the middleware order — which is the one thing in that
file that must stay impossible to miss.

Mechanical, fully covered by the 662 existing tests, and it must not change a
single path or response shape. Do it in one commit, verify with an OpenAPI
schema diff (see **T4**), and make that diff empty.

### R2 — Generate the TypeScript client from OpenAPI

`ui/src/api.ts` hand-maintains ~40 interfaces mirroring Python dataclasses
(`TurnResult`, `VoiceStatus`, `ActionRecord`, …). A backend field rename passes
mypy, passes ruff, passes pytest, passes `tsc` — and breaks the UI at runtime.
It is the only place in the repo where both halves can be green and wrong.

Fix: a script that dumps `app.openapi()` to `ui/src/api-types.gen.ts`
(`openapi-typescript`), plus a CI step that regenerates and fails on a diff.
Keep the hand-written ergonomics (`api.turn()`, `ApiError`, the token logic) —
replace only the type declarations.

Note this pairs with **C3**: bounded `limit` parameters show up in the
generated types as real constraints.

### R3 — Lift the intent tables out of the turn loop — **done**

Six regex/set constants at the top of `orchestrator.py` encode what a user
*means* in a particular language, inside the module that decides what to *do*.
A third language today means editing six literals in the middle of the turn
loop, and **C1** is what that coupling already cost.

```
app/brain/intents/__init__.py   for_language(lang) -> Intents
app/brain/intents/en.py
app/brain/intents/es.py
```

`Intents` is a frozen dataclass of the compiled patterns; `run_turn` asks for
the one matching `config.persona.language` and falls back to `en`. A test
asserts every locale defines every field — which is what would have caught C1.

### R4 — Split `ui/src/api.ts` and `ui/src/i18n.ts`

921 and 751 lines. `api.ts` splits by domain behind one barrel export, so no
call site changes. `i18n.ts` splits to `i18n/en.ts` + `i18n/es.ts` with the
`translate`/`detectLang` logic in `i18n/index.ts`. Key parity is already
enforced by the type system (verified: 300 keys each, tsc clean) — keep that
property across the split, it is doing real work.

### R5 — Leave the data layer alone

Every module writes raw SQL against `db.query`/`db.execute` with no repository
abstraction. That is the right call at this size and it should be stated as a
decision rather than drifted away from: a repository seam earns its keep when
there is a second backend or a need for cross-cutting instrumentation, and
there is neither. Revisit only if **F8** (sync/multi-device) is ever taken.

---

## 4. New capability

Ordered by value ÷ effort. Each names the seam it uses, because the point of the
architecture above is that none of these should need a new one.

### F1 — Two-round routing (plan → act → re-route) ★

Today the router picks ≤3 skills in one shot and never sees their results
before the synthesis pass. "Find the lease, then tell me what the deposit was"
cannot work: the second step depends on the first's output, and the router had
to guess both up front.

Fix: a bounded loop in `run_turn` — route, execute, feed the results back to
the router once, execute again, then synthesise. Cap at 2 rounds. The Action
Gate is untouched (every call still goes through `gate.submit`), `not_run`
already exists for the honesty accounting, and `MAX_SKILLS_PER_TURN` (now
config, per **C7**) becomes a per-round budget.

This is the single biggest jump in what the assistant can actually do, and the
turn loop is already shaped for it.

### F2 — Model autodetect and fallback

`brain.model` is one name. If it is not pulled, every turn returns "run ollama
pull qwen2.5" — the most common first-run failure, and the one the README
spends a paragraph on. `llm.health()` already lists what *is* installed.

Fix: when the configured model is absent, pick the best installed one by a
small preference order, use it, and say once which one was chosen and how to
change it. Fails loudly only when nothing at all is installed.

### F3 — `/privacy/export`

`wipe_all_local_data()` is "all or nothing" in the destructive direction and
there is no constructive counterpart. Portability is the other half of REQ-26:
a user who owns their data should be able to take it.

One zip: memory facts, tasks, notes, transcripts, action history, conversation
turns, and the config with `connectors.*` stripped. JSON plus the existing
Markdown task mirror. No secrets, by construction — `credentials.fetch` is
never called.

### F4 — Per-client notification cursors

The prerequisite for a second window, a browser tab alongside the tray, or
anything web-facing (**C9**). `/events` takes a client id, notifications carry
a monotonic sequence, and each client acknowledges up to a sequence instead of
draining a shared deque. Modest work, and it unblocks several things at once.

### F5 — Grounded document answers

`documents.search` returns passages and the model paraphrases them. The
hallucination guard in the orchestrator catches *unperformed actions* and
*ungrounded external data*, but not "this quoted sentence does not exist in the
retrieved chunk" — on the feature a user is most likely to trust blindly.

Fix: a verification pass that checks each quoted span against the chunks that
were actually retrieved, and marks or drops what is not there. Sits next to
`_guard_ungrounded_reply`, needs no model.

### F6 — Timing surfaced in Diagnostics

There is no instrumentation in the turn loop at all (verified: no
`perf_counter`/`monotonic` anywhere in `brain/` or `main.py`). The performance
numbers that justify the design live in comments; nothing measures the running
app.

Fix: record routing / skill / synthesis milliseconds per turn into an
in-memory ring, expose them on `/diagnostics/timings`, and show a small panel
in Settings. Local only, no telemetry, nothing leaves the machine — consistent
with the privacy posture, and it turns "it feels slow today" into a number.

### F7 — Cross-platform adapter layer

The domain layer is already portable. What is Windows-bound:
`screen/capture.py`, `skills/system/apps.py` (Start Menu, `ctypes.windll`),
`explorer.exe` in [`main.rs:102`](../ui/src-tauri/src/main.rs#L102),
`LOCALAPPDATA` in `settings.data_dir()` (already branched), and the installer.

Fix: an `app/platform/` package with a Windows implementation and explicit
`NotSupported` stubs, selected once at import. Then add `ubuntu-latest` to the
CI matrix for the portable subset. This is the difference between a personal
tool and a product — and it is a week, not a rewrite, because the seams are
nearly all in six files.

### F8 — A documented skill contract for third parties

The registry already discovers any `Skill` subclass under `app/skills/`. What
is missing is a published contract, a scaffold, and — critically — a trust
decision: a third-party skill is arbitrary code running *inside* the Action
Gate's own process, so it cannot be a drop-in folder without a prompt that says
so in those words.

Treat as a product bet, not a refactor. Do not start it before **R1** and
**R2**, because the contract would be published against a surface that is about
to move.

---

## 5. Test and CI hardening

- **T1 — Coverage with a floor.** 662 tests and no number. `pytest-cov` plus
  `--cov-fail-under` set at whatever today measures, minus nothing. The point
  is the ratchet, not the number.
- **T2 — UI tests.** 3 test files (`api`, `Avatar`, `Chat`) for 25 components.
  `Chat.test.tsx` covers the confirmation contract, which is the right thing to
  have covered first. Next: `Settings` (it writes the config file),
  `Accounts` (a secret must never be echoed back into a field),
  `Documents`, `Planner`.
- **T3 — A perf regression test.** The tables in `index/search.py` are the
  spec. Generate 20k synthetic vectors, assert the resident path is taken and
  the query is under a budget. Mark it slow.
- **T4 — OpenAPI snapshot — done.** A committed `openapi.json` and a test that fails
  on any diff. This is what makes **R1** safe to do in one commit, and what
  makes an accidental contract change visible in review.
- **T5 — Parallelise the suite.** ~9 minutes locally against a 25-minute CI
  timeout. Fixtures are already `tmp_path`-isolated per test, so
  `pytest-xdist` should be close to free. Measure before committing to it.
- **T6 — Pin the gate's invariant harder.** `test_action_gate.py` is the most
  important file in the suite. Add a property test over the confirm path:
  for any sequence of submit/confirm/decline/expire, no action executes twice
  and no id executes an action its preview did not describe.

---

## 6. Sequence

Each phase ends green on `ruff` + `mypy` + `pytest` + `tsc --noEmit`, and the
OpenAPI snapshot from T4 is empty-diff except where a phase says otherwise.

**Phase A — corrections. Done.**
C1 (Spanish guard) first, as a shipped-behaviour bug, then C3, C8, C10, C7,
C5's cheap half, C2, C4, C6, and T4's snapshot last so it pins the surface
*before* R1 moves it. 49 tests added, on a branch cut from `main`.
C9 is unchanged — it is a decision to make, not a defect to fix — and C5's
remaining half is deferred to its own migration.

**Phase B — structure. R1 and R3 done; R2, R4 and T1 remain.**
R1 landed with the snapshot diff empty, which is what it was for. It turned up
two things worth knowing: nine of the twelve new modules share a name with the
package they import from, so every function-local `from .capture import …`
silently became a self-import until mypy caught all 28; and one test that
patched `main.catalog` failed rather than passing for the wrong reason, which
is the only reason it was found.

R3 landed with one correction to the plan as written: the tables are not all
per-language. What the *user* types is matched across every locale at once —
an English persona is not a promise about what the person will type — and only
the guard patterns follow the persona. Splitting naively would have stopped
recognising "sí" as agreement, silently. The Spanish locale also gained the
negatives it never had.

Still open in this phase: R2 generated types + CI drift check, R4 UI splits,
T1 coverage floor.

**Phase C — capability (1–2 weeks).**
F2 model fallback (smallest, highest first-run payoff), F3 export, F1
two-round routing, F6 timings, T3 perf test, T2 UI tests alongside.

**Phase D — a product bet, pick one.**
F7 cross-platform, or F4+F8 (multi-client plus a third-party skill contract).
Both are real roadmaps; doing both at once is how neither lands.

Deliberately not planned: a repository abstraction (**R5**), a vector database
(the measured numbers say no), APScheduler (already rejected with a reason in
`tasks.md`), and any cloud LLM path — `allow_cloud_llm` exists and T11.5 is
honest that there is nothing behind it yet. Leave it that way until there is a
reason, and then make it a phase of its own.
