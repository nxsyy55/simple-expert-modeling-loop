"""Codex argv/bootstrap and owned process-tree cleanup helpers."""
import argparse
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_REASONING_EFFORT = "high"
ALLOWED_MODELS = ("gpt-6-luna", "gpt-6.1-sol", "gpt-5.6-terra")


def codex_executable(explicit=None):
    configured = explicit or os.environ.get("CODEX_CLI_PATH") or shutil.which("codex")
    if not configured:
        raise FileNotFoundError("Codex CLI missing; set CODEX_CLI_PATH or pass --codex")
    path = Path(configured).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Codex executable does not exist: {path}")
    return path.resolve()


def check_host(cli):
    """Exercise CLI bootstrap without creating a session or sending a prompt.

    A child's sandbox flag cannot grant rights denied to its Windows parent.
    CLI help can exit zero while reporting an unusable PATH-alias directory.
    """
    result = subprocess.run([str(cli), "--no-daemon", "exec", "--help"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=30)
    if result.returncode or "could not create PATH aliases" in result.stderr:
        raise RuntimeError("Codex host bootstrap failed before model dispatch. Launch the software "
                           "from a terminal with the required host permissions. "
                           + result.stderr.strip())
    return {"status": "host_preflight_passed", "cli": str(cli), "model_called": False}


def build_command(cli, model, workspace, run_dir, *, role=None, effort=None):
    """Construct the one-session argv; caller must already confirm launch."""
    command = [
        str(cli), "--no-daemon", "exec", "--ignore-user-config", "--skip-git-repo-check",
        "--cd", str(workspace), "--model", model,
        "--sandbox", "danger-full-access", "--config", 'approval_policy="never"',
        "--enable", "shell_tool", "--enable", "unified_exec",
        "--disable", "multi_agent", "--config", 'web_search="disabled"',
        "--ephemeral", "--json", "--output-last-message", str(run_dir / "final.txt"), "-",
    ]
    if effort is not None or model == "gpt-5.6-terra":
        command[-1:-1] = ["--config", "model_reasoning_effort=" + json.dumps(effort or DEFAULT_REASONING_EFFORT)]
    if role == "portfolio_review":
        # The controller supplies the KB inline and writes the returned update.
        # Ordinary file reads support on-demand research review; writes stay disabled.
        command[command.index("--sandbox") + 1] = "read-only"
        for feature in ("multi_agent", "multi_agent_v2",
                        "skill_search", "apps", "plugins", "browser_use", "browser_use_external",
                        "computer_use", "image_generation", "view_image", "workspace_dependencies",
                        "memories", "code_mode"):
            command[-1:-1] = ["--disable", feature]
    return command


def _event_failure(stdout):
    failures = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            failures.append({"malformed_event": line[:500]})
            continue
        if not isinstance(event, dict):
            failures.append({"malformed_event": line[:500]})
            continue
        kind = str(event.get("type", "")).lower()
        if kind in {"error", "turn.failed", "turn_failed"}:
            failures.append(event)
        elif kind == "event_msg":
            payload = event.get("payload") or {}
            subtype = str(payload.get("type", "")).lower() if isinstance(payload, dict) else ""
            if subtype in {"error", "turn.failed", "turn_failed"}:
                failures.append(event)
    return failures


def _process_tree_cleanup(process):
    """Stop a snapshot of an owned process tree, including detached sessions.

    This is best-effort cleanup, not containment: already orphaned processes or
    descendants created during enumeration cannot be proven absent.
    """
    import psutil

    result = {"attempted": True, "method": "psutil process-tree snapshot",
              "command_exit_code": None, "parent_exit_code": None,
              "parent_exited": False, "error": None, "observed_pids": []}
    errors = []
    targets = []
    try:
        if process.poll() is not None:
            raise RuntimeError("Parent already exited; descendant ownership is unknown")
        parent = psutil.Process(process.pid)
        targets = parent.children(recursive=True) + [parent]
        result["observed_pids"] = [p.pid for p in targets]
        for target in reversed(targets):
            try:
                target.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as exc:
                errors.append(f"{type(exc).__name__}: pid {target.pid}")
        _, alive = psutil.wait_procs(targets, timeout=5)
        # POSIX zombies have terminated but await reaping by their new parent.
        alive = [p for p in alive if p.is_running() and p.status() != psutil.STATUS_ZOMBIE]
        if alive:
            errors.append("Processes still alive: " + str([p.pid for p in alive]))
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    try:
        if process.poll() is None:
            process.kill()
        result["parent_exit_code"] = process.wait(timeout=5)
        result["parent_exited"] = True
    except Exception as exc:
        errors.append(f"wait: {type(exc).__name__}: {exc}")
    result["error"] = "; ".join(errors) or None
    result["tree_termination_complete"] = bool(targets and result["parent_exited"] and not errors)
    result["command_exit_code"] = 0 if result["tree_termination_complete"] else None
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Codex CLI bootstrap check; no model call")
    parser.add_argument("action", choices=["check-host"])
    parser.add_argument("--codex")
    args = parser.parse_args(argv)
    print(json.dumps(check_host(codex_executable(args.codex))))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
