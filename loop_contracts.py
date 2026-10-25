"""Cross-check existing control/plan/result JSON; freeze one derived task view.

No second writable controller or scientific interpretation lives here. Historical
expert plans stay immutable; pending/completed are derived from C01 and C03/delta.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

from loop_controller import atomic_json, read_json, validate_state

FORMAT = "loop-json-state-v1"
VIEW = "workspace/history/state.json"
POLICY = ("current_round", "target_rounds", "review_every", "review_cycle_start", "models")


def published_plan_matches(runtime, knowledge, review_task, manifest):
    """Permit recorded operator additions without rewriting an expert's receipt."""
    if manifest["files"]["accumulated.json"]["new"] == digest(runtime / "knowledge/accumulated.json"):
        return True
    staged = review_task / "publication/accumulated.json"
    if not staged.is_file() or digest(staged) != manifest["files"]["accumulated.json"]["new"]:
        return False
    published = read_json(staged)
    if {k:v for k,v in published.items() if k != "next_tasks"} != {k:v for k,v in knowledge.items() if k != "next_tasks"}:
        return False
    current = {x["task_id"]:x for x in knowledge["next_tasks"]}
    if any(current.get(x["task_id"]) != x for x in published["next_tasks"]):
        return False
    original_ids = {x["task_id"] for x in published["next_tasks"]}
    return all(x["task_id"] in original_ids or x.get("origin") == "user"
               or x.get("user_required") is True for x in knowledge["next_tasks"])


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def feedback_matches(packet, feedback):
    if (set(feedback) != {"task_id", "status", "score", "artifacts", "error"}
            or feedback["task_id"] != packet["task_id"]
            or feedback["status"] not in {"success", "duplicate", "model_failed"}
            or packet.get("role") not in {"luna", "review"}):
        raise ValueError("C02/C03 identity or terminal status disagrees")
    score = feedback["score"]
    if packet["role"] == "luna" and feedback["status"] == "success":
        if type(score) not in {int, float} or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("Successful research C03 requires a finite formal score")
    elif score is not None:
        raise ValueError("Failed/duplicate research and expert C03 must not carry a score")


def result_matches(task, packet, feedback):
    """Validate formal JSON only; never predict, fit, or reinterpret a failure."""
    feedback_matches(packet, feedback)
    if packet["role"] != "luna":
        return
    from session_train import _validate_existing
    receipt = _validate_existing(Path(task), packet)
    if receipt is None:
        raise ValueError("C03 requires its bound training receipt")
    expected = {"success": "success", "duplicate": "duplicate", "model_failure": "model_failed", "timeout": "model_failed"}
    if expected.get(receipt.get("status")) != feedback["status"]:
        raise ValueError("C03 status disagrees with the training receipt")
    metric_path = Path(task) / "workspace/output/evaluation/metrics.json"
    metric = read_json(metric_path) if metric_path.is_file() else None
    if feedback["status"] == "success":
        if (not metric or metric.get("run_id") != packet["task_id"] or metric.get("status") != "success"
                or metric.get("roc_auc") != feedback["score"] or metric.get("reload_verified") is not True):
            raise ValueError("C03 and formal Eva result disagree")
        if metric.get("training_input_sha256") != digest(Path(task) / "input.json") or metric.get("split_sha256") != packet["data"]["split_sha256"]:
            raise ValueError("Eva is not bound to the task scientific identity")
        if metric.get("predictions_sha256") != digest(metric_path.parent / "predictions.npy"):
            raise ValueError("Formal prediction file changed")
        receipt_path = Path(task) / "workspace/training/receipt.json"
        strict = bool(packet.get("workflow"))
        if strict and not receipt_path.is_file():
            raise ValueError("New successful C03 is missing its training receipt")
        if receipt_path.is_file():
            receipt = read_json(receipt_path)
            if receipt.get("status") != "success" or receipt.get("probe_reload_verified") is False:
                raise ValueError("Successful C03 disagrees with training/reload receipt")
            if strict and receipt.get("probe_reload_verified") is not True:
                raise ValueError("New C03 requires the training probe reload receipt")
            source_sha = receipt.get("source_sha256")
            if (strict and (not isinstance(source_sha, str) or len(source_sha) != 64)
                    or source_sha is not None and source_sha != metric.get("train_sha256")):
                raise ValueError("Receipt and Eva source identity disagree")
            if strict:
                if (metric.get("training_source_sha256") != source_sha
                        or metric.get("training_status") != "success"
                        or receipt.get("training_input_sha256") != digest(Path(task) / "input.json")):
                    raise ValueError("Receipt/Eva do not bind the same frozen C02 and source")
                from session_train import artifact_identity
                actual_artifacts = artifact_identity(Path(task) / "workspace/training/artifacts")
                if (actual_artifacts != receipt.get("artifacts")
                        or actual_artifacts != (metric.get("artifacts") or {}).get("sha256")):
                    raise ValueError("Training receipt/Eva do not match actual artifact bytes")
                for name in ("workspace/train.py", "workspace/training/train.py", "workspace/output/evaluation/train.py"):
                    path = Path(task) / name
                    if not path.is_file() or digest(path) != source_sha:
                        raise ValueError("Successful C03 source file is missing or changed: " + name)
                for key in ("probe_predictions_sha256", "probe_reload_predictions_sha256"):
                    if not receipt.get(key) or receipt[key] != metric.get(key):
                        raise ValueError("Receipt and Eva probe identity disagree")
                if not isinstance(receipt.get("artifacts"), dict) or not receipt["artifacts"] or receipt["artifacts"] != (metric.get("artifacts") or {}).get("sha256"):
                    raise ValueError("Receipt and Eva artifact identity disagree")
    elif metric and metric.get("status") == "success":
        raise ValueError("Failed/duplicate C03 contradicts a successful formal Eva result")


def _runtime_view(runtime):
    """Read only; an active task's C03/delta is uncommitted until C01 advances."""
    runtime = Path(runtime)
    path = runtime / "control.json"
    if not path.is_file():
        return None  # Legacy standalone preparation has no controller to reconcile.
    state = validate_state(read_json(path))
    if state["target_rounds"] is not None and state["current_round"] > state["target_rounds"]:
        raise ValueError("C01 accepted rounds exceed its saved target")
    if state["status"] == "completed" and (state["active_task"] is not None
            or state["target_rounds"] is None or state["current_round"] < state["target_rounds"]):
        raise ValueError("Completed C01 disagrees with active task or target")
    knowledge_path = runtime / "knowledge/accumulated.json"
    knowledge = read_json(knowledge_path)
    plan, review = knowledge["next_tasks"], knowledge["review"]
    if (not isinstance(plan, list) or not isinstance(review, dict)
            or not isinstance(review.get("covered_delta_ids"), list)
            or len(set(review["covered_delta_ids"])) != len(review["covered_delta_ids"])):
        raise ValueError("Invalid structured expert plan/coverage")
    ids = [item["task_id"] for item in plan]
    if len(ids) != len(set(ids)):
        raise ValueError("Expert plan repeats a task ID")
    for item in plan:
        if (not isinstance(item.get("task_id"), str) or not item["task_id"]
                or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in item["task_id"])
                or not isinstance(item.get("question"), str) or not item["question"].strip()
                or not isinstance(item.get("parent_source"), str)
                or not isinstance(item.get("evidence"), list) or any(not isinstance(x, str) for x in item["evidence"])):
            raise ValueError("Invalid structured plan task fields")
    records = []
    active = state["active_task"]
    for feedback_path in (runtime / "tasks").glob("*/feedback.json"):
        task = feedback_path.parent
        packet = read_json(task / "input.json")
        feedback = read_json(feedback_path)
        if packet.get("task_id") != task.name:
            raise ValueError("Task directory and C02 identity disagree")
        feedback_matches(packet, feedback)
        if task.name == active or packet["role"] == "review":
            continue
        delta_path = task / "delta.json"
        if not delta_path.is_file():
            raise ValueError("C03 has no active C01 task or committed delta: " + task.name)
        delta = read_json(delta_path)
        if delta.get("task_id") != task.name or delta.get("feedback_ref") != "feedback.json":
            raise ValueError("Delta and formal feedback identity disagree")
        settlement = delta.get("settlement")
        if not settlement:
            raise ValueError("Committed research requires explicit settlement identity")
        if (settlement["input_sha256"] != digest(task / "input.json")
                           or settlement["feedback_sha256"] != digest(feedback_path)):
            raise ValueError("Settled C02/C03 bytes changed")
        result_matches(task, packet, feedback)
        records.append(dict(task_id=task.name, status=feedback["status"], score=feedback["score"],
            round=settlement["research_round"] if settlement else None,
            legacy_order=(delta_path.stat().st_mtime_ns, task.name),
            question=(packet.get("provenance") or {}).get("question"),
            parent_source=(packet.get("provenance") or {}).get("parent_source")))
    # A dangling delta cannot silently consume a plan or advance the counter.
    for path in (runtime / "tasks").glob("*/delta.json"):
        if path.parent.name != active and not (path.parent / "feedback.json").is_file():
            raise ValueError("Delta has no paired C03")
    if len(records) != state["current_round"]:
        raise ValueError("C01 round count disagrees with committed research C03/deltas")
    explicit = {r["round"]: r for r in records}
    if len(explicit) != len(records) or set(explicit) != set(range(1, len(records)+1)):
        raise ValueError("Conflicting or missing research settlement rounds")
    ordered = [explicit[n] for n in range(1, len(records)+1)]
    for record in ordered:
        record.pop("legacy_order")
    by_id = {r["task_id"]: r for r in ordered}
    covered = review["covered_delta_ids"]
    if not set(covered) <= set(by_id):
        raise ValueError("Expert coverage references uncommitted or unknown research")
    pending, completed = [], []
    for item in plan:
        if item["task_id"] in by_id:
            executed = by_id[item["task_id"]]
            if executed["question"] is not None and (executed["question"] != item["question"]
                    or executed["parent_source"] != item["parent_source"]):
                raise ValueError("Completed plan disagrees with executed C02 question/parent")
            completed.append({**item, "result": by_id[item["task_id"]]})
        elif item["task_id"] != active:
            pending.append(item)
    if active:
        packet = read_json(runtime / "tasks" / active / "input.json")
        if packet.get("task_id") != active or packet.get("role") not in {"luna", "review"}:
            raise ValueError("C01 active task disagrees with C02")
        if state["next_step"] == "evaluate" and packet["role"] != "luna":
            raise ValueError("Expert cannot enter research evaluation")
    elif state["status"] in {"running", "stopping"}:
        raise ValueError("Running C01 has no active task")
    origin = state.get("review_cycle_start")
    due = bool(state["review_every"] and origin is not None
               and state["current_round"] - origin >= state["review_every"])
    if origin is not None and not active and state["next_step"] != ("review" if due else "luna"):
        raise ValueError("C01 next step disagrees with expert cadence")
    identifier = review["task_id"]
    if identifier:
        review_task = runtime / "tasks" / identifier
        if (review_task / "input.json").is_file():
            review_packet = read_json(review_task / "input.json")
            if (review_packet.get("review") or {}).get("protocol") == "expert-files-v1":
                manifest = read_json(review_task / "publication/manifest.json")
                staged_path = review_task / "publication/accumulated.json"
                staged = read_json(staged_path)
                if (digest(staged_path) != manifest["files"]["accumulated.json"]["new"]
                        or manifest["assigned_task_ids"] != [x["task_id"] for x in staged["next_tasks"]]
                        or staged["review"]["task_id"] != identifier):
                    raise ValueError("Expert manifest/staged plan IDs disagree")
                receiving = identifier == active and manifest.get("status") == "prepared"
                if (manifest.get("status") != "complete" and not receiving or manifest.get("task_id") != identifier
                        or manifest.get("input_sha256") != digest(review_task / "input.json")
                        or not receiving and not published_plan_matches(runtime, knowledge, review_task, manifest)):
                    raise ValueError("Live structured plan disagrees with expert publication")
    compact = [{k:r[k] for k in ("task_id", "round", "status", "score")} for r in ordered]
    return dict(format=FORMAT, control=state, plan=dict(review_task_id=identifier,
        pending_tasks=pending, completed_tasks=completed), latest_research=ordered[-1] if ordered else None,
        completed_research=compact, uncovered_delta_ids=sorted(set(by_id) - set(covered)),
        review_due=due, knowledge_json_sha256=digest(knowledge_path))


def runtime_view(runtime):
    """Observers retry only reads if a writer crossed an atomic C01 boundary."""
    runtime = Path(runtime)
    def identity():
        return tuple(digest(path) if path.is_file() else None for path in
                     (runtime / "control.json", runtime / "knowledge/accumulated.json"))
    for _ in range(3):
        before = identity()
        try:
            result = _runtime_view(runtime)
        except (ValueError, KeyError, FileNotFoundError):
            if identity() == before:
                raise
        else:
            if identity() == before:
                return result
    raise ValueError("JSON authority changed during inspection; read again at a stable boundary")


def freeze(task, snapshot, *, question=None):
    """A frozen projection, never another writable live state file."""
    if snapshot is None:
        return None
    if snapshot["control"]["active_task"] is not None:
        raise ValueError("Cannot prepare another C02 while C01 has an active task")
    value = {**snapshot, "snapshot_stage": "before_dispatch", "current_task": dict(task_id=Path(task).name,
        role="review" if question is None else "luna",
        origin="expert_plan" if question in snapshot["plan"]["pending_tasks"] else "autonomous",
        question=question)}
    if question is None:
        value["current_task"].update(origin="expert_review")
    atomic_json(Path(task) / VIEW, value)
    return dict(file=VIEW, sha256=digest(Path(task) / VIEW))


def execution_brief(task, packet):
    """One explicit task authority, displayed before scientific reference text."""
    binding = packet.get("workflow")
    if not binding:
        return None
    frozen = read_json(Path(task) / binding["file"])
    return dict(authority="C02.workflow/current_task", current_task=frozen["current_task"],
        pending_tasks=frozen["plan"]["pending_tasks"],
        completed_plan_results=[dict(task_id=x["task_id"],question=x["question"],
            status=x["result"]["status"],score=x["result"]["score"]) for x in frozen["plan"]["completed_tasks"]],
        latest_research=frozen["latest_research"],
        rule="Knowledge prose and historical next-task headings are references, never a current assignment. "
             "Execute only current_task; an autonomous task with no pending plan must select a new evidence-based mechanism or explicit new condition.")


def validate_dispatch(task, packet, *, allow_legacy_receive=False):
    """Reconcile frozen C02 with live authority before any paid invocation."""
    binding = packet.get("workflow")
    if binding is None:
        raise ValueError("Release tasks require a frozen workflow; legacy tasks are not imported")
    path = Path(task) / binding["file"]
    if binding["file"] != VIEW or digest(path) != binding["sha256"]:
        raise ValueError("Frozen JSON workflow changed")
    frozen = read_json(path)
    if (binding["file"] not in packet["knowledge_files"] or frozen["current_task"]["role"] != packet["role"]
            or (packet["role"] == "review" and (packet.get("training") is not None or packet.get("data") is not None))):
        raise ValueError("C02 role/tool mapping disagrees with its frozen JSON workflow")
    live = runtime_view(Path(task).parent.parent)
    if (live is None or any(frozen["control"].get(k) != live["control"].get(k) for k in POLICY)
            or frozen["knowledge_json_sha256"] != live["knowledge_json_sha256"]
            or frozen["completed_research"] != live["completed_research"]
            or frozen["uncovered_delta_ids"] != live["uncovered_delta_ids"]
            or frozen["current_task"]["task_id"] != packet["task_id"]
            or live["control"]["active_task"] not in {None, packet["task_id"]}):
        raise ValueError("Prepared C02 is stale relative to C01/structured expert plan")
    if packet["role"] == "luna":
        question = frozen["current_task"]["question"]
        if frozen["review_due"]:
            raise ValueError("Expert is due before another research task")
        if frozen["current_task"]["origin"] == "expert_plan" and question not in live["plan"]["pending_tasks"]:
            # An active task is intentionally absent from the pending queue.
            if live["control"]["active_task"] != packet["task_id"]:
                raise ValueError("Planned task is already consumed or changed")
        provenance = packet["provenance"]
        if (provenance["question"] != question["question"] or provenance["parent_source"] != question["parent_source"]
                or provenance.get("review_task_id") != frozen["plan"]["review_task_id"]):
            raise ValueError("C02 question/parent disagrees with structured task selection")
    else:
        contract = packet["review"]
        if (contract["unfinished_tasks"] != frozen["plan"]["pending_tasks"]
                or {x["task_id"] for x in contract["increments"]} != set(frozen["uncovered_delta_ids"])):
            raise ValueError("Expert C02 pending plan/coverage disagrees with JSON workflow")
    configured = frozen["control"].get("models", {}).get(packet["role"], {})
    if any((packet.get("session") or {}).get(key) != value for key, value in configured.items()):
        raise ValueError("C02 session disagrees with configured role model")
