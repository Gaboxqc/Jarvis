"""Terminal client — the Phase 0-5 stand-in for the Tauri UI.

Runs the orchestrator in-process, so it exercises the same code the API does.
It also demonstrates the confirmation contract from the client side: the pending
action_id is held here and handed back when the user says yes. Saying "yes" with
nothing pending does nothing at all.
"""

from __future__ import annotations

import sys
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import focus
from .actions import gate, journal, undo
from .brain import llm, orchestrator
from .index import scanner as index_scanner
from .index import store as index_store
from .memory import long_term
from .scheduler import service as scheduler
from .scheduler import store as sched_store
from .settings import load_config
from .skills.registry import catalog, load_skills
from . import db

console = Console()
SESSION = "cli"


def _banner() -> None:
    config = load_config()
    health = llm.health()
    skills = catalog()

    lines = [f"[bold]{config.persona.name}[/bold] — personal desktop assistant"]
    if health.get("ok"):
        lines.append(f"[green]●[/green] brain: {health['model']} via Ollama")
    else:
        lines.append(f"[red]●[/red] brain unavailable: {health.get('error')}")
        lines.append("[dim]  Local commands below still work.[/dim]")
    lines.append(f"[dim]{len(skills)} skills · data in {db.db_path().parent}[/dim]")
    lines.append("")
    lines.append("[dim]/skills /memory /history /pending /reminders /docs /reindex[/dim]"
    )
    lines.append(
        "[dim]/voice /listen /speak /focus /undo /health /wipe /quit[/dim]")

    console.print(Panel("\n".join(lines), border_style="cyan"))


def _on_reminder(delivery: scheduler.Delivery) -> None:
    console.print()
    console.print(Panel(delivery.message(), title="⏰", border_style="yellow"))
    console.print("[bold cyan]you ›[/bold cyan] ", end="")


def _print_skills() -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("skill")
    table.add_column("gated", justify="center")
    table.add_column("what it does")
    for entry in catalog():
        table.add_row(
            entry["name"],
            "[yellow]yes[/yellow]" if entry["consequential"] else "[dim]no[/dim]",
            entry["description"].split(".")[0][:70],
        )
    console.print(table)


def _print_memory() -> None:
    facts = long_term.all_facts()
    if not facts:
        console.print("[dim]Nothing stored.[/dim]")
        return
    for fact in facts:
        console.print(f"  [dim]{fact.id[:8]}[/dim]  [{fact.category}] {fact.text}")


def _print_history() -> None:
    records = journal.history(limit=15)
    if not records:
        console.print("[dim]No actions yet.[/dim]")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("when")
    table.add_column("action")
    table.add_column("status")
    for record in records:
        when = record.executed_at.astimezone().strftime("%d %b %H:%M") if record.executed_at else "—"
        colour = {"executed": "green", "failed": "red", "undone": "yellow",
                  "declined": "dim", "expired": "dim"}.get(record.status, "white")
        undoable = " [dim](undoable)[/dim]" if record.can_undo else ""
        table.add_row(when, record.preview[:70] + undoable, f"[{colour}]{record.status}[/{colour}]")
    console.print(table)


def _print_pending() -> None:
    items = journal.pending()
    if not items:
        console.print("[dim]Nothing waiting for approval.[/dim]")
        return
    for item in items:
        console.print(f"  [yellow]{item.id[:8]}[/yellow]  {item.preview}")


def _print_reminders() -> None:
    items = sched_store.active_items()
    if not items:
        console.print("[dim]Nothing scheduled.[/dim]")
        return
    for item in items:
        console.print(f"  {item.describe()}")


def _print_documents() -> None:
    status = index_scanner.status()
    if not status["folders"]:
        console.print("[dim]No folders configured for document search "
                      "(documents.indexed_folders).[/dim]")
        return

    console.print(f"[bold]{status['documents']}[/bold] documents "
                  f"([dim]{status['chunks']} passages[/dim]) from "
                  f"{', '.join(status['folders'])}")
    if status["deferred_because"]:
        console.print(f"[yellow]Indexing paused: {status['deferred_because']}[/yellow]")
    elif status["running"]:
        console.print("[dim]A scan is running.[/dim]")

    problems = index_store.failures()
    if problems:
        console.print(f"[yellow]{len(problems)} could not be read:[/yellow]")
        for problem in problems[:10]:
            console.print(f"  [dim]{problem['file']}[/dim] - {problem['error']}")


def _print_health() -> None:
    config = load_config()
    health = llm.health()
    console.print(f"  brain     : {'ok' if health.get('ok') else health.get('error')}")
    console.print(f"  model     : {health.get('model')} @ {health.get('host')}")
    console.print(f"  skills    : {len(catalog())}")
    console.print(f"  config    : {config.source_path}")
    console.print(f"  data      : {db.db_path()}")
    console.print(f"  web search: {'on' if config.privacy.allow_web_search else 'off'}")
    console.print(f"  file roots: {', '.join(str(r) for r in config.system.allowed_roots) or 'none'}")


def _print_voice() -> None:
    from .voice import models as voice_models
    from .voice import session as voice_session

    status = voice_session.status()
    state = "on" if status["enabled"] else "off"
    console.print(f"  voice     : [bold]{state}[/bold] "
                  f"(in={'on' if status['input_enabled'] else 'off'}, "
                  f"out={'on' if status['output_enabled'] else 'off'})")
    console.print(f"  microphone: {'found' if status['microphone'] else '[red]none[/red]'}")
    console.print(f"  speech    : {status['stt']['model']} "
                  f"({'installed' if status['stt']['installed'] else '[yellow]not downloaded[/yellow]'}, "
                  f"{'loaded' if status['stt']['loaded'] else 'unloaded'})")
    console.print(f"  reply voice: {status['tts']['voice']} "
                  f"({'installed' if status['tts']['installed'] else '[yellow]not downloaded[/yellow]'})")
    console.print(f"  wake word : {status['wake']['phrase']} "
                  f"({'enabled' if status['wake']['enabled'] else 'off'}, "
                  f"{'installed' if status['wake']['installed'] else 'not downloaded'})")
    if status["tts"]["available"]:
        console.print(f"  voices    : {', '.join(status['tts']['available'])}")
    if not status["models_ready"]:
        console.print(f"  [yellow]Run /voice setup to download "
                      f"~{voice_models.total_download_mb()}MB of models.[/yellow]")


def _voice_setup() -> None:
    from .voice import models as voice_models

    pending = voice_models.missing()
    if not pending:
        console.print("[green]All voice models are already downloaded.[/green]")
        return

    total = sum(entry.approx_mb for entry in pending)
    console.print(f"[bold]This will download about {total}MB:[/bold]")
    for entry in pending:
        console.print(f"  {entry.name} ({entry.kind}, ~{entry.approx_mb}MB)")
    console.print(f"[dim]Saved to {voice_models.models_root()}[/dim]")
    if console.input("Continue? [y/N] ").strip().lower() not in {"y", "yes"}:
        console.print("[dim]Cancelled.[/dim]")
        return

    with console.status("[dim]downloading...[/dim]", spinner="dots"):
        report = voice_models.ensure_all()
    for name in report["downloaded"]:
        console.print(f"[green]downloaded[/green] {name}")
    for failure in report["failed"]:
        console.print(f"[red]failed[/red] {failure['model']}: {failure['error']}")
    if report["ready"]:
        console.print("[green]Voice is ready. Set voice.enabled: true in kai.config.yaml.[/green]")


def _listen_once() -> None:
    from .voice import audio as voice_audio
    from .voice.session import VoiceSession

    if not load_config().voice.enabled:
        console.print("[yellow]Voice is off. Set voice.enabled: true in kai.config.yaml.[/yellow]")
        return
    if not voice_audio.has_microphone():
        console.print("[red]No microphone found.[/red]")
        return

    labels = {"calibrating": "listening to the room", "listening": "go ahead",
              "speaking": "hearing you", "thinking": "thinking", "idle": ""}
    console.print("[cyan]Speak now — I'll stop when you pause.[/cyan]")
    turn = VoiceSession(on_state=lambda s: console.print(f"[dim]{labels.get(s, s)}[/dim]")
                        if labels.get(s) else None).listen_once()

    if turn.heard:
        console.print(f"[dim]heard ({turn.confidence:.0%} sure):[/dim] {turn.heard}")
    if turn.error and not turn.reply:
        console.print(f"[yellow]{turn.error}[/yellow]")
    if turn.reply:
        console.print(f"[bold green]{load_config().persona.name} ›[/bold green] {turn.reply}")


def _speak(text: str) -> None:
    from .voice import tts

    if not text.strip():
        console.print("[dim]Usage: /speak something to say[/dim]")
        return
    try:
        speech = tts.speak(text)
    except tts.TTSUnavailable as exc:
        console.print(f"[red]{exc}[/red]")
        return
    if speech is None:
        console.print("[yellow]Speech output is off (voice.enabled / output_enabled).[/yellow]")
    else:
        console.print(f"[dim]spoke {speech.seconds:.1f}s[/dim]")


def _handle_command(command: str) -> bool:
    """Returns False to exit. These work with or without the model running."""
    verb, _, argument = command.partition(" ")
    argument = argument.strip()

    if verb == "/voice":
        _voice_setup() if argument == "setup" else _print_voice()
        return True
    if verb == "/listen":
        _listen_once()
        return True
    if verb == "/speak":
        _speak(argument)
        return True

    match command:
        case "/quit" | "/exit" | "/q":
            return False
        case "/skills":
            _print_skills()
        case "/memory":
            _print_memory()
        case "/history":
            _print_history()
        case "/pending":
            _print_pending()
        case "/reminders":
            _print_reminders()
        case "/docs":
            _print_documents()
        case "/reindex":
            with console.status("[dim]scanning documents...[/dim]", spinner="dots"):
                result = index_scanner.scan(force=True)
            console.print(f"[green]{result.summary()}[/green] "
                          f"[dim]({result.duration_seconds:.1f}s)[/dim]")
        case "/focus":
            state = focus.state()
            console.print(state.describe())
        case "/health":
            _print_health()
        case "/undo":
            result = undo.undo_last()
            console.print(f"[{'green' if result.ok else 'red'}]{result.message}[/]")
        case "/wipe":
            console.print("[bold red]Delete all local data — conversations, memories, "
                          "history, reminders, tasks?[/bold red]")
            if console.input("Type DELETE to confirm: ").strip() == "DELETE":
                removed = db.wipe_all_local_data()
                total = sum(removed.values())
                console.print(f"[green]Removed {total} records.[/green]")
            else:
                console.print("[dim]Cancelled.[/dim]")
        case _:
            console.print(f"[dim]Unknown command {command}[/dim]")
    return True


def main() -> int:
    load_skills()
    scheduler.subscribe(_on_reminder)
    scheduler.start()

    # Deliver anything that came due while the app was closed (REQ-9).
    for delivery in scheduler.tick():
        console.print(Panel(delivery.message(), title="⏰ missed", border_style="yellow"))

    _banner()

    pending_id: str | None = None
    try:
        while True:
            try:
                text = console.input("[bold cyan]you ›[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                break

            if not text:
                continue
            if text.startswith("/"):
                if not _handle_command(text):
                    break
                continue

            with console.status("[dim]thinking…[/dim]", spinner="dots"):
                result = orchestrator.handle_turn(text, SESSION, pending_action_id=pending_id)

            name = load_config().persona.name
            if result.needs_confirmation and result.pending:
                console.print(Panel(result.reply, title=f"{name} needs a yes",
                                    border_style="yellow"))
                pending_id = result.pending.action_id
            else:
                style = "red" if result.error else "green"
                console.print(f"[bold {style}]{name} ›[/bold {style}] {result.reply}")
                pending_id = None
    finally:
        scheduler.stop()
        db.close_connection()

    console.print("[dim]bye[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
