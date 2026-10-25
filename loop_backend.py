"""Generic project backend for C02 research and file-only expert reviews."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import pandas as pd

import production_session
from loop_controller import ROOT, atomic_json, read_json
from loop_storage import (input_path, readonly_paths, reference, store_file,
                          verify_readonly, write_history)
from project_config import load_config, resolve

TOOLS = ROOT / ".venv"
PYTHON = Path(sys.executable).resolve()
DEFAULT_RESEARCH_REFERENCES = ("templates/project/program.md", "templates/project/knowledge/research.md")
SESSION_TIMEOUT_SECONDS = 60 * 60


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def under(root: Path, value: str | Path) -> Path:
    path = Path(value)
    path = path.resolve() if path.is_absolute() else (Path(root) / path).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("Path escaped the project/task root")
    return path


def append_event(path: Path, **event) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        stream.flush()


def session_events(task: Path) -> list[dict]:
    path = Path(task) / "session.log"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def add_research_context(command: list[str], task: Path, *, full_access: bool = False) -> list[str]:
    packet_path = Path(task) / "input.json"
    packet = read_json(packet_path)
    workspace = Path(task) / "workspace"
    command[command.index("exec") + 1:command.index("exec") + 1] = ["--strict-config", "--ignore-rules"]
    # Codex runs with a read-only filesystem sandbox; all writes and child jobs
    # are mediated by the task-local JSON argv transport.
    command[command.index("--sandbox") + 1] = "danger-full-access" if full_access else "read-only"
    for feature in ("shell_tool", "unified_exec"):
        while ["--enable", feature] in [command[i:i+2] for i in range(len(command)-1)]:
            i = next(i for i in range(len(command)-1) if command[i:i+2] == ["--enable", feature])
            del command[i:i+2]
    for feature in ("shell_tool", "unified_exec", "shell_snapshot", "memories", "apps", "plugins",
                    "skill_search", "multi_agent", "multi_agent_v2", "browser_use", "browser_use_external",
                    "computer_use", "image_generation", "code_mode", "workspace_dependencies"):
        command[-1:-1] = ["--disable", feature]
    command[-1:-1] = ["--config", 'project_root_markers=["input.json"]', "--config", "project_doc_max_bytes=0"]
    config = {"command": str(PYTHON), "args": ["-B", str(ROOT / "research_transport.py"),
        "--input", str(packet_path), "--project-root", str(ROOT)],
        "cwd": str(task), "required": True, "startup_timeout_sec": 20,
        "tool_timeout_sec": 45,
        "enabled_tools": ["files"] if packet["role"] == "review" else
                         ["files", "python", "training", "training_status", "wait"],
        "default_tools_approval_mode": "auto"}
    for key, value in config.items():
        command[-1:-1] = ["--config", "mcp_servers.research_io." + key + "=" + json.dumps(value)]
    return command


class ResearchBackend:
    default_references = DEFAULT_RESEARCH_REFERENCES

    def __init__(self, *, authorized_full_access=False, baseline_authorized=False):
        self.authorized_full_access = bool(authorized_full_access)
        self.baseline_authorized = bool(baseline_authorized)
        self.models = {}
        self.config = load_config()

    def configure(self, models):
        self.models = models

    def validate_runtime(self, runtime):
        from loop_contracts import runtime_view
        path = Path(runtime) / "knowledge/accumulated.json"
        if path.is_file():
            runtime_view(runtime)

    def validate_feedback(self, task, feedback):
        from loop_contracts import result_matches
        if not self.returned(task):
            raise ValueError("C03 has no verified returned model session")
        self._validate_process_events(Path(task))
        result_matches(task, read_json(Path(task) / "input.json"), feedback)

    def session_settings(self, role):
        defaults = self.config["evaluation"]
        settings = dict(self.models.get(role, {}))
        if role == "review" and not settings.get("model"):
            settings["model"] = defaults.get("expert_model")
        settings.setdefault("model", defaults.get("research_model", production_session.DEFAULT_MODEL))
        settings.setdefault("effort", defaults.get("research_effort", production_session.DEFAULT_REASONING_EFFORT))
        if not settings.get("model"):
            raise ValueError("Configure the expert model before enabling reviews")
        return settings

    def _split(self):
        from prepare import prepare
        return prepare(ROOT, verify_only=True)

    def _ensure_split(self):
        from prepare import prepare
        return prepare(ROOT, verify_only=False)

    def _candidate(self) -> Path:
        value = self.config.get("candidate", {}).get("source", "templates/project/train.py")
        candidate = resolve(self.config, value)
        if not candidate.is_file():
            raise FileNotFoundError(f"Candidate source is missing: {candidate}")
        return candidate

    def results(self, runtime):
        split = self._split()
        rows = []
        paths = [ROOT / "workspace/baseline/workspace/output/evaluation/metrics.json",
                 *Path(runtime).glob("tasks/*/workspace/output/evaluation/metrics.json")]
        for metric_path in paths:
            if not metric_path.is_file():
                continue
            metric = read_json(metric_path)
            source = metric_path.parent.parent.parent / "train.py" if "baseline" in metric_path.parts else metric_path.parent.parent.parent / "train.py"
            if metric.get("split_sha256") != split["split_sha256"]:
                continue
            if not source.is_file() or metric.get("train_sha256") != digest(source):
                raise ValueError(f"Recorded candidate source changed: {source}")
            rows.append((metric_path, source, {k: metric.get(k) for k in (
                "run_id", "hypothesis", "status", "roc_auc", "reload_verified", "error",
                "raw_sha256", "split_sha256", "packages", "train_sha256")}))
        rows.sort(key=lambda row: (row[0].stat().st_mtime_ns, str(row[0])))
        return rows

    def archived_results(self):
        return []

    def initialize(self, runtime):
        runtime = Path(runtime)
        if not runtime.resolve().is_relative_to(ROOT):
            raise ValueError("Runtime must stay inside this project's workspace directory")
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "tasks").mkdir(exist_ok=True)
        knowledge_path = runtime / "knowledge/accumulated.json"
        if not knowledge_path.exists():
            atomic_json(knowledge_path, {"next_tasks": [], "optional_files": [],
                "review": {"task_id": None, "covered_delta_ids": []}})
            (runtime / "knowledge/research.md").write_text(
                (ROOT / "templates/project/knowledge/research.md").read_text(encoding="utf-8"), encoding="utf-8")
        from loop_contracts import runtime_view
        runtime_view(runtime)
        try:
            self._split()
        except FileNotFoundError:
            if not self.baseline_authorized:
                raise ValueError("No prepared split exists. Run `loop_cli.py prepare`, then start an explicit `run`.")
            self._ensure_split()
        baseline = ROOT / "workspace/baseline"
        marker = baseline / "workspace/output/evaluation/metrics.json"
        if marker.is_file():
            metric = read_json(marker)
            if metric.get("split_sha256") != read_json(ROOT / "workspace/data/prepared/split.json")["split_sha256"]:
                raise ValueError("Baseline was evaluated on another split; preserve its evidence")
            if digest(baseline / "workspace/train.py") != metric.get("train_sha256"):
                raise ValueError("Baseline source identity changed")
            from session_train import _validate_existing
            receipt = _validate_existing(baseline, read_json(baseline / "input.json"))
            if not receipt or receipt.get("status") != "success" or metric.get("status") != "success":
                raise ValueError("Baseline requires a successful bound training/Eva receipt")
            return
        if not self.baseline_authorized:
            raise ValueError("No verified baseline exists. Start with `loop_cli.py run` to authorize the local baseline step.")
        self._run_baseline(baseline)

    def _run_baseline(self, task: Path):
        if task.exists():
            input_path = task / "input.json"
            receipt_path = task / "workspace/training/receipt.json"
            if not input_path.is_file() or not receipt_path.is_file():
                raise ValueError("Incomplete baseline evidence exists; preserve it and do not retrain")
            from session_train import _validate_existing
            existing = _validate_existing(task, read_json(input_path))
            if not existing or existing.get("status") != "success":
                raise ValueError("Existing baseline has no verified successful training receipt")
            metric_path = task / "workspace/output/evaluation/metrics.json"
            if metric_path.is_file():
                metric = read_json(metric_path)
                if metric.get("split_sha256") != read_json(ROOT / "workspace/data/prepared/split.json").get("split_sha256"):
                    raise ValueError("Existing baseline Eva belongs to another split")
                return
            self._run_eva(task)
            return
        else:
            task.mkdir(parents=True)
        workspace = task / "workspace"
        data_dir = workspace / "data"
        data_dir.mkdir(parents=True)
        source = self._candidate()
        shutil.copyfile(source, workspace / "train.py")
        split = read_json(ROOT / "workspace/data/prepared/split.json")
        shutil.copyfile(ROOT / "workspace/data/prepared/research.csv", data_dir / "train.csv")
        probe = pd.read_csv(data_dir / "train.csv").head(min(32, split["research_rows"]))
        probe.to_csv(data_dir / "probe.csv", index=False)
        packet = {"task_id": "baseline", "role": "luna", "data": {"train": "workspace/data/train.csv",
            "probe": "workspace/data/probe.csv", "target": self.config["data"]["target"],
            "id": self.config["data"].get("id"), "split_sha256": split["split_sha256"],
            "research_sha256": digest(data_dir / "train.csv"), "probe_sha256": digest(data_dir / "probe.csv")},
            "training": {"budget_seconds": 1200, "known_source_sha256": []}}
        atomic_json(task / "input.json", packet)
        from session_train import run_workspace
        run_workspace(task / "input.json")
        self._run_eva(task)
        metric_path = workspace / "output/evaluation/metrics.json"
        metric = read_json(metric_path)
        if metric.get("status") != "success":
            raise RuntimeError("Baseline Eva did not produce a success receipt")
        metric["hypothesis"] = "Shipped logistic regression baseline"
        atomic_json(metric_path, metric)

    def _run_eva(self, task: Path):
        workspace = Path(task) / "workspace"
        command = [str(PYTHON), str(ROOT / "evaluate.py"), "--project-root", str(ROOT),
                   "--task", str(task), "--output", str(workspace / "output/evaluation")]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=600)
        if result.returncode:
            raise RuntimeError("Independent Eva failed: " + result.stderr[-4000:])

    def freeze_review_evidence(self, runtime, task, question, knowledge):
        identifier = knowledge["review"]["task_id"]
        if not identifier:
            return []
        origin = Path(runtime) / "tasks" / identifier
        packet_path = origin / "input.json"
        if not packet_path.is_file():
            return []
        packet = read_json(packet_path)
        if (packet.get("review") or {}).get("protocol") != "expert-files-v1":
            return []
        manifest = read_json(origin / "publication/manifest.json")
        if manifest.get("status") != "complete" or not self.returned(origin):
            raise ValueError("Expert evidence has no completed manifest-bound publication")
        allowed = set(readonly_paths(origin, packet))
        refs = question.get("evidence", []) if question.get("task_id") in manifest["assigned_task_ids"] else []
        result = []
        for ref in refs:
            source = input_path(origin, ref)
            if source not in allowed:
                raise ValueError("Expert citation is not in the frozen review packet")
            result.append({"reference": ref, "path": store_file(task, source),
                           "sha256": digest(source), "review_task_id": identifier})
        return result

    def prepare(self, runtime, role, every):
        runtime = Path(runtime).resolve()
        if role == "review":
            from expert_review import prepare
            return prepare(self, runtime, every, ROOT)
        if role != "luna":
            raise ValueError("Unknown research role")
        from loop_contracts import freeze, runtime_view
        from loop_storage import write_history
        knowledge = read_json(runtime / "knowledge/accumulated.json")
        view = runtime_view(runtime)
        completed = [row for row in self.results(runtime) if row[2].get("status") == "success" and row[2].get("reload_verified") is True]
        if not completed:
            raise ValueError("No verified candidate is available as a parent")
        plan = (view["plan"]["pending_tasks"] if view else knowledge["next_tasks"])
        question = plan[0] if plan else None
        keeper = max(completed, key=lambda row: row[2]["roc_auc"])
        if question is None:
            question = {"task_id": "luna-" + uuid.uuid4().hex[:16],
                "question": "Review the latest formal result and propose one testable change to the candidate, with evidence and a stop condition.",
                "parent_source": keeper[1].relative_to(ROOT).as_posix(), "evidence": []}
        identifier = question["task_id"]
        if not identifier.startswith("luna-") or not identifier[5:].isalnum():
            raise ValueError("Research task ID must use luna-<unique id>")
        task = runtime / "tasks" / identifier
        if task.exists():
            if session_events(task):
                raise ValueError("Task has a session record; never replay it")
            if (task / "input.json").is_file():
                return task
        workspace = task / "workspace"
        (workspace / "output/tmp").mkdir(parents=True, exist_ok=True)
        data_dir = workspace / "data"
        data_dir.mkdir(exist_ok=True)
        split = self._split()
        research = pd.read_csv(ROOT / "workspace/data/prepared/research.csv")
        shutil.copyfile(ROOT / "workspace/data/prepared/research.csv", data_dir / "research.csv")
        research.head(min(32, len(research))).to_csv(data_dir / "probe.csv", index=False)
        research_sha = digest(data_dir / "research.csv")
        parent = under(ROOT, question["parent_source"])
        if not parent.is_file() or parent not in {row[1].resolve() for row in completed}:
            raise ValueError("Selected parent is not a verified candidate in this evaluation domain")
        shutil.copyfile(parent, workspace / "train.py")
        history = []
        for metric_path, source, metric in self.results(runtime):
            history.append({**metric, "source_file": store_file(task, source),
                "canonical_source": source.relative_to(ROOT).as_posix(), "comparable_to_current": True,
                "split_sha256": split["split_sha256"]})
        write_history(task, history)
        exposed = []
        for path in (runtime / "knowledge/research.md", runtime / "knowledge/accumulated.json",
                     ROOT / "templates/project/program.md", ROOT / "templates/project/tools/README.md",
                     ROOT / "templates/project/tools/example.py", ROOT / "pyproject.toml", ROOT / "uv.lock"):
            if path.is_file():
                exposed.append(store_file(task, path))
        allowed_optional = set(self.default_references) | {x.relative_to(ROOT).as_posix() for x in (ROOT / "references").glob("*.md")}
        for name in knowledge.get("optional_files", []):
            if name not in allowed_optional:
                raise ValueError("Optional knowledge reference is outside the exposed catalog")
            exposed.append(store_file(task, under(ROOT, name)))
        sources = [{"role": "parent", "path": store_file(task, parent)},
                   {"role": "best", "path": store_file(task, keeper[1])}]
        evidence_files = self.freeze_review_evidence(runtime, task, question, knowledge)
        exposed.extend(item["path"] for item in evidence_files)
        training = {"command": [str(PYTHON), str(ROOT / "session_train.py"), "--input", str(task / "input.json")],
            "status_command": [str(PYTHON), str(ROOT / "session_train.py"), "--input", str(task / "input.json"), "--status"],
            "budget_seconds": 1200, "max_duplicate_revisions": 0,
            "known_fingerprints": [__import__("session_train").source_fingerprint(row[1]) for row in self.results(runtime)],
            "known_source_sha256": [row[2]["train_sha256"] for row in self.results(runtime)
                                    if isinstance(row[2].get("train_sha256"), str)],
            "outputs": {k: {**v, "exists": (task / v["path"]).is_file()} for k, v in __import__("session_train").output_paths(workspace).items()}}
        packet = {"task_id": identifier, "role": "luna", "task_file": "workspace/task.md",
            "source_files": sources, "knowledge_files": list(dict.fromkeys([*exposed, "workspace/history/index.json"])),
            "data": {"train": "workspace/data/research.csv", "probe": "workspace/data/probe.csv",
                     "target": self.config["data"]["target"], "id": self.config["data"].get("id"),
                     "split_sha256": split["split_sha256"], "research_sha256": research_sha, "probe_sha256": digest(data_dir / "probe.csv")}, "training": training,
            "tool_space": {"execution": {"protocol": "json-argv-v1", "tools": ["files", "python", "training", "training_status", "wait"]}},
            "provenance": {"review_task_id": knowledge["review"]["task_id"],
                "knowledge_sha256": digest(runtime / "knowledge/accumulated.json"), "parent_source": question["parent_source"],
                "parent_sha256": digest(parent), "question": question["question"],
                "evidence": question.get("evidence", []), "evidence_files": evidence_files}}
        binding = freeze(task, view, question=question) if view else None
        if binding:
            packet["workflow"] = binding
            packet["knowledge_files"].append(binding["file"])
        task_text = ("Execute only the current task. Read the frozen C02 workflow before research notes. "
            "Edit workspace/train.py with one evidence-backed binary-classification hypothesis. "
            "The only fitting entry is training; use fit_predict(train_x, train_y, predict_x, model_dir) "
            "and persist artifacts. Candidate code must also implement predict_saved(model_dir, frame). "
            "Do not access the evaluator holdout, install packages, start other agents, or change framework files.\n")
        (workspace / "task.md").write_text(task_text, encoding="utf-8")
        packet["session"] = self.session_settings("luna")
        atomic_json(task / "input.json", packet)
        return task

    def command(self, task):
        packet = read_json(Path(task) / "input.json")
        readonly_paths(task, packet)
        cli = production_session.codex_executable()
        session = packet.get("session") or self.session_settings(packet["role"])
        role = "portfolio_review" if packet["role"] == "review" else None
        command = production_session.build_command(cli, session["model"], task,
            Path(task) / "workspace/output", role=role, effort=session["effort"])
        return add_research_context(command, Path(task), full_access=self.authorized_full_access)

    def preflight(self, task):
        from loop_contracts import validate_dispatch
        packet = read_json(Path(task) / "input.json")
        validate_dispatch(task, packet)
        if packet.get("role") not in {"luna", "review"}:
            raise ValueError("Unknown task role")
        production_session.check_host(production_session.codex_executable())
        self.command(task)

    def build_prompt(self, task, packet=None):
        packet = packet or read_json(Path(task) / "input.json")
        from loop_contracts import execution_brief
        brief = execution_brief(task, packet)
        prefix = "EXECUTION AUTHORITY (read first):\n" + json.dumps(brief, ensure_ascii=False) + "\n" if brief else ""
        return prefix + ("Use only the frozen task packet and its listed evidence. The controller owns identity, budget, "
            "evaluation, history and recovery. Do not start another agent/session or edit runtime/control state.\nC02:\n" +
            json.dumps(packet, ensure_ascii=False) + "\nTask:\n" +
            under(task, packet["task_file"]).read_text(encoding="utf-8"))

    def invoke(self, task, pulse, log):
        from loop_contracts import validate_dispatch
        packet = read_json(Path(task) / "input.json")
        validate_dispatch(task, packet)
        command = self.command(task)
        immutable = {reference(task, p): digest(p) for p in readonly_paths(task, packet)}
        output = Path(task) / "workspace/output"
        output.mkdir(parents=True, exist_ok=True)
        event_path = Path(task) / "session.log"
        with (output / "stdout.jsonl").open("w", encoding="utf-8") as stdout, (output / "stderr.log").open("w", encoding="utf-8") as stderr:
            try:
                process = subprocess.Popen(command, cwd=task, stdin=subprocess.PIPE, stdout=stdout,
                    stderr=stderr, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    start_new_session=os.name != "nt")
            except OSError:
                append_event(event_path, event="not_started", readonly=immutable)
                raise
            append_event(event_path, event="started", pid=process.pid, readonly=immutable)
            try:
                process.stdin.write(self.build_prompt(task, packet)); process.stdin.close()
                deadline = time.monotonic() + SESSION_TIMEOUT_SECONDS
                while process.poll() is None:
                    pulse()
                    if time.monotonic() > deadline:
                        production_session._process_tree_cleanup(process)
                        append_event(event_path, event="unknown", reason="timeout; no retry")
                        raise RuntimeError("Codex result is unknown after timeout; task retained")
                    time.sleep(0.2)
            except BaseException:
                if process.poll() is None:
                    production_session._process_tree_cleanup(process)
                raise
        verify_readonly(task, immutable)
        stdout_text = (output / "stdout.jsonl").read_text(encoding="utf-8", errors="replace")
        if process.returncode or production_session._event_failure(stdout_text):
            append_event(event_path, event="failed", returncode=process.returncode)
            raise RuntimeError("Codex session failed; never automatically replay")
        if not (output / "final.txt").is_file():
            raise RuntimeError("Codex returned without its final artifact")
        append_event(event_path, event="returned", returncode=process.returncode)

    def returned(self, task):
        events = session_events(task)
        if not events or events[-1].get("event") != "returned":
            return False
        originals = next((x.get("readonly") for x in events if x.get("event") == "started"), {})
        try:
            verify_readonly(task, originals)
            return bool(originals)
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def not_started(self, task):
        events = session_events(task)
        if not events or events[-1].get("event") != "not_started":
            return False
        try:
            verify_readonly(task, events[-1]["readonly"])
            return True
        except Exception:
            return False

    def finish(self, task, pulse, log):
        packet = read_json(Path(task) / "input.json")
        if not self.returned(task):
            raise RuntimeError("No verifiably returned Codex session")
        self._validate_process_events(Path(task))
        if packet["role"] == "review":
            if not all(under(task, packet["review"][key]).is_file()
                       for key in ("research_file", "plan_file")):
                raise ValueError("Expert did not write both required staged files")
            return self.feedback(task, "success", None, None)
        receipt_path = Path(task) / "workspace/training/receipt.json"
        if not receipt_path.is_file():
            raise RuntimeError("Research session has no training receipt; no automatic retrain")
        from session_train import _validate_existing
        receipt = _validate_existing(Path(task), packet)
        if receipt.get("status") in {"model_failure", "timeout"}:
            return self.feedback(task, "model_failed", None, receipt.get("error"))
        if receipt.get("status") == "duplicate":
            return self.feedback(task, "duplicate", None, receipt.get("error"))
        if receipt.get("status") != "success":
            raise RuntimeError("Training outcome is unknown; preserve task")
        output = Path(task) / "workspace/output/evaluation"
        if (output / "metrics.json").is_file():
            metric = read_json(output / "metrics.json")
            feedback = dict(task_id=Path(task).name, status="success", score=metric.get("roc_auc"), artifacts="workspace/output", error=None)
            self.validate_feedback(task, feedback)
            return self.feedback(task, "success", metric["roc_auc"], None)
        command = [str(PYTHON), str(ROOT / "evaluate.py"), "--project-root", str(ROOT), "--task", str(task), "--output", str(output)]
        log("Eva launches as a separate process and loads the saved candidate.")
        with (Path(task) / "workspace/output/eva.log").open("w", encoding="utf-8") as stream:
            process = subprocess.Popen(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            deadline = time.monotonic() + 600
            while process.poll() is None:
                pulse()
                if time.monotonic() > deadline:
                    production_session._process_tree_cleanup(process)
                    raise RuntimeError("Eva timed out; preserve saved training without replay")
                time.sleep(0.2)
        metrics_path = output / "metrics.json"
        if process.returncode or not metrics_path.is_file():
            raise RuntimeError("Eva failed; retain training and source evidence")
        metrics = read_json(metrics_path)
        if metrics.get("status") != "success" or metrics.get("reload_verified") is not True:
            raise RuntimeError("Eva result is not a verified saved-model evaluation")
        return self.feedback(task, "success", metrics["roc_auc"], None)

    @staticmethod
    def _validate_process_events(task: Path):
        path = Path(task) / "workspace/output/process-events.jsonl"
        if not path.is_file():
            return
        diagnostics = []
        blocked = []
        started = set()
        completed = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            kind = event.get("event")
            if kind == "rejected":
                if (event.get("tool") == "files" and not event.get("process_started")
                        and event.get("action") in {"read", "list", "stat", "search"}):
                    diagnostics.append(event)
                else:
                    blocked.append(event)
            elif kind == "started":
                started.add(event.get("job_id"))
            elif kind == "completed":
                completed.add(event.get("job_id"))
                if event.get("kind") == "inspection" and event.get("exit_code") != 0:
                    blocked.append(event)
                elif event.get("exit_code") not in (None, 0):
                    blocked.append(event)
            elif kind == "interrupted":
                blocked.append(event)
        if (started - completed) - {None}:
            blocked.append({"unsettled_job_ids": sorted(started - completed)})
        if blocked:
            raise RuntimeError("Tool process evidence contains a nonzero, interrupted, or unresolved operation; preserve the task")
        if diagnostics:
            atomic_json(Path(task) / "workspace/output/diagnostics.json",
                        dict(read_only_file_errors=diagnostics, evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))

    def feedback(self, task, status, score, error):
        result = {"task_id": Path(task).name, "status": status, "score": score,
                  "artifacts": "workspace/output", "error": error}
        atomic_json(Path(task) / "feedback.json", result)
        return result

    def delta(self, task, feedback):
        return {"task_id": Path(task).name, "feedback_ref": "feedback.json"}

    def publish(self, task):
        from expert_review import publish
        return publish(task, ROOT)
