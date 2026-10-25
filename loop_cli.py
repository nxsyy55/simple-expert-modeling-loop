"""Command-line entry for serial research and file-based expert review."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys
from typing import Sequence

from loop_controller import DEFAULT_RUNTIME, LoopController

TARGET_ROUNDS = 10
EXIT_OK = 0
EXIT_BLOCKED = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the supervised Simple Loop research workflow.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "continue", "review"):
        command = commands.add_parser(name, help="start, continue, or explicitly request a review")
        command.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME,
                             help="C01/C02 runtime directory")
        command.add_argument("--rounds", type=_positive_rounds, default=None,
                             help="cumulative accepted Luna round target; defaults to 10 only on first run")
        command.add_argument("--authorized-full-access", action="store_true",
                             help="explicitly select the host Codex CLI full-access path")
    status = commands.add_parser("status", help="show the saved controller state")
    status.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME,
                        help="C01/C02 runtime directory")
    audit = commands.add_parser("audit", help="read-only reconciliation of control, expert plan and formal results; no calls")
    audit.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    configure = commands.add_parser("configure", help="save role models and review cadence at an idle boundary; no calls or training")
    configure.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    configure.add_argument("--review-every", type=_nonnegative, default=None, help="0 disables periodic review; positive N starts a fresh period from current count")
    for role in ("research", "expert"):
        configure.add_argument("--" + role + "-model", default=None)
        configure.add_argument("--" + role + "-effort", choices=("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"), default=None)
    prepare = commands.add_parser("prepare", help="create or verify the fixed data split; no model call or fitting")
    prepare.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parent)
    return parser


def _nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("review interval must be nonnegative")
    return number


def _positive_rounds(value: str) -> int:
    try:
        rounds = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("rounds must be a positive integer") from exc
    if rounds < 1:
        raise argparse.ArgumentTypeError("rounds must be a positive integer")
    return rounds


def _run(args: argparse.Namespace) -> int:
    from loop_backend import ResearchBackend

    runtime = args.runtime.resolve()
    if not runtime.is_relative_to(Path(__file__).resolve().parent / "workspace"):
        raise ValueError("Runtime must stay under this release workspace")
    controller = LoopController(runtime, backend=ResearchBackend(
        authorized_full_access=args.authorized_full_access,
        baseline_authorized=args.command == "run"))
    # `run` is safe to repeat: an existing C01 resumes without resetting rounds.
    has_control = (runtime / "control.json").is_file()
    mode = "review" if args.command == "review" else "continue" if has_control or args.command == "continue" else "new"
    saved = controller.read_state() if has_control else None
    target_rounds = (args.rounds if args.rounds is not None else
                     saved["target_rounds"] if saved is not None else TARGET_ROUNDS)
    # Preserve saved cycle origin. A legacy six-field review_every was disabled
    # compatibility data, not authorization to start periodic expert calls.

    previous_handler = signal.getsignal(signal.SIGINT)

    def stop_on_sigint(_signal_number, _frame):
        print("Ctrl+C received; finishing the current Luna round before stopping.", file=sys.stderr)
        controller.request_stop()

    signal.signal(signal.SIGINT, stop_on_sigint)
    try:
        state = controller.run(mode=mode, target_rounds=target_rounds)
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    print(json.dumps(state, ensure_ascii=False, indent=2))
    if state.get("status") in {"completed", "stopped"}:
        return EXIT_OK
    print(f"Loop ended in state: {state.get('status', 'unknown')}", file=sys.stderr)
    return EXIT_BLOCKED


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "configure":
            if not args.runtime.resolve().is_relative_to(Path(__file__).resolve().parent / "workspace"):
                raise ValueError("Runtime must stay under this release workspace")
            models = {}
            for label, role in (("research", "luna"), ("expert", "review")):
                config = {key: getattr(args, label + "_" + key) for key in ("model", "effort")
                          if getattr(args, label + "_" + key) is not None}
                if config:
                    models[role] = config
            if args.review_every is None and not models:
                raise ValueError("Specify review cadence or a role model/effort")
            state = LoopController(args.runtime).configure(review_every=args.review_every, models=models)
            print(json.dumps(state, ensure_ascii=False, indent=2))
            return EXIT_OK
        if args.command == "prepare":
            from prepare import prepare
            plan = prepare(args.project_root)
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return EXIT_OK
        if args.command == "status":
            state = LoopController(args.runtime).read_state()
            print(json.dumps(state, ensure_ascii=False, indent=2))
            return EXIT_OK
        if args.command == "audit":
            from loop_contracts import runtime_view
            if (args.runtime / "control.json").is_file() and not (args.runtime / "knowledge/accumulated.json").exists():
                state = LoopController(args.runtime).read_state()
                if state["current_round"] or state["active_task"]:
                    raise ValueError("Nonempty runtime is missing knowledge")
                print(json.dumps(dict(initialized=False, control=state), ensure_ascii=False))
                return EXIT_OK
            print(json.dumps(runtime_view(args.runtime), ensure_ascii=False, indent=2))
            return EXIT_OK
        return _run(args)
    except (KeyboardInterrupt, Exception) as exc:
        print(f"Loop CLI error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
