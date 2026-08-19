"""Entry point for the packaged backend — REQ-29.

The CLI form (`uvicorn app.main:app`) needs a Python interpreter and an import
path, neither of which exists once frozen. This starts the same app in-process
so PyInstaller has a real script to build from.

It is also the process the desktop app spawns as a sidecar, so its failure modes
are user-visible: a port already in use has to say so plainly rather than dying
with a traceback the user cannot act on.
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import socket
import sys
import threading

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8756

# Below this, packaging has silently dropped capabilities (see _selftest).
MIN_EXPECTED_SKILLS = 40


def ensure_standard_streams() -> None:
    """Give uvicorn's logging setup somewhere to write.

    The bundle is built with `console=False`, so a windowed process that nobody
    piped has `sys.stdout` and `sys.stderr` set to None. uvicorn's default log
    config builds a StreamHandler on each of them, and `logging.dictConfig`
    fails on the None with

        ValueError: Unable to configure formatter 'default'

    which kills the backend before it binds the port, with no console to print
    the traceback to. The desktop app never hit it because tauri-plugin-shell
    pipes both streams; running the executable directly does hit it, and so does
    piping only one of the two.
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


# Set by the desktop app when it spawns this as a sidecar. Absent when someone
# runs the executable themselves, where exiting on a closed stdin would be
# baffling rather than helpful.
PARENT_WATCH_ENV = "KAI_PARENT_WATCH"


def watch_parent() -> None:
    """Exit when whoever launched this process goes away.

    The desktop app already kills the sidecar on its own exit, and that covers
    every path it can see: the tray menu, the taskbar, a logout. It cannot cover
    the paths where it never runs -- a crash, a kill, an Application Control
    policy stopping the binary mid-flight. Then the backend is orphaned: it
    keeps port 8756, holds its own DLLs open so an upgrade cannot overwrite
    them, and answers requests for an app that is no longer running. All three
    happened, and the third is the worst, because nothing about it looks broken.

    Reading the inherited stdin is how the child learns. The pipe stays open and
    empty for as long as the parent lives, and reaches EOF the moment it dies,
    whatever killed it -- so this needs no polling, no process handles and no
    platform-specific code.

    `os._exit` rather than a graceful shutdown: there is nothing left to be
    graceful for, the parent is already gone, and the database is journalled
    precisely so an abrupt stop is recoverable. Hanging around to close sockets
    tidily is how the orphan gets created in the first place.
    """
    stream = getattr(sys, "stdin", None)
    if stream is None:
        return

    def wait() -> None:
        try:
            stream.read()
        except Exception:  # noqa: BLE001 - a broken pipe means the same thing
            pass
        os._exit(0)

    threading.Thread(target=wait, name="parent-watch", daemon=True).start()


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


def main(argv: list[str] | None = None) -> int:
    # PyInstaller re-executes the bundle for each child process; without this a
    # frozen app that touches multiprocessing forks itself indefinitely.
    multiprocessing.freeze_support()
    # Before anything can try to log. uvicorn configures logging during run().
    ensure_standard_streams()

    parser = argparse.ArgumentParser(prog="kai-backend", description="Kai backend service")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--log-level", default="info")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="Report what this build can actually do, then exit.",
    )
    args = parser.parse_args(argv)

    # Bound to loopback and refused otherwise. This service reaches the user's
    # files, mail and calendar; it must never be listening on a LAN (REQ-26).
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print(
            f"Refusing to listen on {args.host}. Kai's backend is loopback-only.",
            file=sys.stderr,
        )
        return 2

    if args.selftest:
        return _selftest()

    if not port_is_free(args.host, args.port):
        print(
            f"Port {args.port} is already in use. Kai may already be running - "
            f"check the system tray before starting another copy.",
            file=sys.stderr,
        )
        return 3

    # Armed before the server starts, so a parent that dies during startup is
    # noticed too.
    if os.environ.get(PARENT_WATCH_ENV):
        watch_parent()

    import uvicorn

    from app.main import app

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


def _selftest() -> int:
    """Check that a packaged build kept the parts that are easy to lose.

    Skills are discovered at runtime, so freezing can drop every one of them
    while leaving an app that starts, serves and answers -- the failure is
    invisible from the outside. This makes it visible, and non-zero on failure
    so a build script can refuse to ship it.
    """
    from app.skills.registry import discovered_module_names, load_errors, load_skills

    frozen = getattr(sys, "frozen", False)
    discovered = discovered_module_names()
    skills = load_skills()
    problems = load_errors()

    print(f"frozen        : {frozen}")
    print(f"modules found : {len(discovered)}")
    for name in discovered:
        print(f"    {name}")
    print(f"skills loaded : {len(skills)}")
    for name in sorted(skills):
        print(f"  {name}")

    if problems:
        # The whole point: in a packaged build these are otherwise invisible.
        print(f"\nskipped ({len(problems)}):")
        for name, reason in problems:
            print(f"  {name}")
            print(f"      {reason}")

    if len(skills) < MIN_EXPECTED_SKILLS:
        print(
            f"\nFAIL: expected {MIN_EXPECTED_SKILLS}+ skills, found {len(skills)}. "
            "Skill discovery did not survive packaging.",
            file=sys.stderr,
        )
        return 1

    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
