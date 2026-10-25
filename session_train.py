"""Bounded candidate worker, receipt recovery, and fresh-process reload checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
import pandas as pd


def source_fingerprint(path: Path) -> str:
    import ast
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8-sig"))
    except SyntaxError:
        return sha(path)
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def artifact_identity(directory: Path) -> dict:
    directory = Path(directory).resolve()
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("predict_entry") != "train.py:predict_saved" or not manifest.get("files"):
        raise ValueError("Artifact manifest must bind train.py:predict_saved and files")
    hashes = {"manifest.json": sha(manifest_path)}
    for name in manifest["files"]:
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or path == directory or not path.is_file() or path.stat().st_size == 0:
            raise ValueError("Artifact is missing or escapes its artifact directory")
        hashes[name] = sha(path)
    return hashes


def _receipt(task: Path) -> Path:
    return task / "workspace/training/receipt.json"


def _validate_existing(task: Path, packet: dict) -> dict | None:
    path = _receipt(task)
    if not path.is_file():
        return None
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved.get("task_id") != task.name or saved.get("training_input_sha256") != sha(task / "input.json"):
        raise ValueError("Existing receipt belongs to another task/input; preserve it")
    source = task / "workspace/train.py"
    if not source.is_file() or saved.get("source_sha256") != sha(source):
        raise ValueError("Existing receipt belongs to another candidate source; preserve it")
    for key, name in (("research_sha256", "train"), ("probe_sha256", "probe")):
        if packet["data"].get(key) and sha(task / packet["data"][name]) != packet["data"][key]:
            raise ValueError("Task data changed after dispatch")
    if saved.get("status") == "success":
        source = task / "workspace/train.py"
        if not source.is_file() or sha(source) != saved.get("source_sha256"):
            raise ValueError("Existing successful receipt has a different candidate source")
        if not (task / "workspace/training/train.py").is_file() or sha(task / "workspace/training/train.py") != saved["source_sha256"]:
            raise ValueError("Frozen training source differs from the receipt")
        if artifact_identity(task / "workspace/training/artifacts") != saved.get("artifacts"):
            raise ValueError("Saved artifact bytes differ from the receipt")
        for operation, key in (("fit", "probe_predictions_sha256"), ("predict", "probe_reload_predictions_sha256")):
            values = np.load(task / f"workspace/training/{operation}_predictions.npy", allow_pickle=False)
            if hashlib.sha256(values.tobytes()).hexdigest() != saved.get(key):
                raise ValueError("Saved probe prediction bytes changed")
        return saved
    if saved.get("status") in {"model_failure", "timeout", "duplicate"}:
        return saved
    raise ValueError("Existing receipt has an unknown status; refusing to rerun")


def _worker(task: Path, packet: dict, operation: str, *, budget: int) -> np.ndarray:
    workspace = task / "workspace"
    training = workspace / "training"
    request = {"operation": operation, "source": str(workspace / "train.py"),
        "source_sha256": sha(workspace / "train.py"), "model_dir": str(training / "artifacts"),
        "train_csv": str(task / packet["data"]["train"]),
        "predict_csv": str(task / packet["data"]["probe"]), "target": packet["data"]["target"],
        "id": packet["data"].get("id"), "output_npy": str(training / (operation + "_predictions.npy"))}
    request_path = training / (operation + "_request.json")
    atomic_json(request_path, request)
    command = [sys.executable, "-B", str(Path(__file__).with_name("candidate_worker.py")), "--request", str(request_path)]
    process = subprocess.Popen(command, cwd=task, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                               start_new_session=os.name != "nt")
    try:
        stdout, stderr_bytes = process.communicate(timeout=budget)
    except subprocess.TimeoutExpired:
        from production_session import _process_tree_cleanup
        cleanup = _process_tree_cleanup(process)
        if not cleanup["tree_termination_complete"]:
            raise RuntimeError("Worker termination unknown; preserve task without a terminal receipt")
        raise
    if process.returncode:
        stderr = stderr_bytes.decode("utf-8", errors="replace")[-4000:]
        raise RuntimeError("candidate_worker_failed: " + stderr)
    return np.load(request["output_npy"], allow_pickle=False)


def run_workspace(input_path: Path) -> dict:
    input_path = Path(input_path).resolve()
    task = input_path.parent
    packet = json.loads(input_path.read_text(encoding="utf-8"))
    if packet.get("task_id") != task.name or packet.get("role") != "luna":
        raise ValueError("Training requires the matching research C02")
    existing = _validate_existing(task, packet)
    if existing is not None:
        return existing
    workspace = task / "workspace"
    source = workspace / "train.py"
    source_sha = sha(source)
    initial_input_sha = sha(input_path)
    train_path, probe_path = task / packet["data"]["train"], task / packet["data"]["probe"]
    train, probe = pd.read_csv(train_path), pd.read_csv(probe_path)
    if packet["data"].get("research_sha256") and sha(train_path) != packet["data"]["research_sha256"]:
        raise ValueError("Frozen research data bytes changed")
    target = packet["data"]["target"]
    if target not in train or train[target].isna().any() or train[target].nunique() != 2:
        raise ValueError("Training data must have a nonmissing binary target")
    excluded = {target, packet["data"].get("id")}
    if list(train.drop(columns=[x for x in excluded if x and x in train]).columns) != list(
            probe.drop(columns=[x for x in excluded if x and x in probe]).columns):
        raise ValueError("Training/probe feature schemas differ")
    training = workspace / "training"
    receipt_path = training / "receipt.json"
    training.mkdir(parents=True, exist_ok=True)
    budget = int(packet["training"].get("budget_seconds", 1200))
    if budget <= 0:
        raise ValueError("Training budget must be positive")
    duplicate_hashes = set(packet["training"].get("known_source_sha256", []))
    if source_sha in duplicate_hashes or source_fingerprint(source) in packet["training"].get("known_fingerprints", []):
        receipt = {"schema": 1, "task_id": task.name, "status": "duplicate",
            "source_sha256": source_sha, "training_input_sha256": sha(input_path), "error": "duplicate_source"}
        atomic_json(receipt_path, receipt)
        return receipt
    artifacts = training / "artifacts"
    if artifacts.exists():
        raise ValueError("Training artifact directory already exists without a receipt; refusing overwrite")
    artifacts.mkdir()
    (training / "train.py").write_bytes(source.read_bytes())
    try:
        started = time.monotonic()
        predictions = _worker(task, packet, "fit", budget=budget)
        fit_seconds = time.monotonic() - started
        if len(predictions) != len(probe):
            raise ValueError("Candidate fit output has the wrong row count")
        model_files = sorted(p.relative_to(artifacts).as_posix() for p in artifacts.rglob("*") if p.is_file() and p.name != "manifest.json")
        if not model_files:
            raise ValueError("Candidate did not persist model files")
        atomic_json(artifacts / "manifest.json", {"schema": 1, "family": "candidate",
            "files": model_files, "predict_entry": "train.py:predict_saved"})
        artifact_before_reload = artifact_identity(artifacts)
        if sha(source) != source_sha or sha(training / "train.py") != source_sha:
            raise ValueError("Candidate source changed during training")
        atomic_json(training / "probe.json", {"rows": len(probe), "predictions_sha256": hashlib.sha256(predictions.tobytes()).hexdigest()})
        remaining = int(budget - fit_seconds)
        if remaining < 1:
            raise subprocess.TimeoutExpired("candidate_worker", budget)
        reloaded = _worker(task, packet, "predict", budget=remaining)
        if sha(source) != source_sha or sha(training / "train.py") != source_sha or sha(input_path) != initial_input_sha:
            raise ValueError("Candidate changed frozen source or input during reload")
        if artifact_identity(artifacts) != artifact_before_reload:
            raise ValueError("Fresh-process predict_saved changed saved artifact bytes")
        if reloaded.shape != predictions.shape or not np.allclose(predictions, reloaded, rtol=1e-9, atol=1e-11):
            raise ValueError("Fresh-process saved model did not reproduce probe predictions")
        pred_sha = hashlib.sha256(predictions.tobytes()).hexdigest()
        reload_sha = hashlib.sha256(reloaded.tobytes()).hexdigest()
        receipt = {"schema": 1, "task_id": task.name, "status": "success", "source_sha256": source_sha,
            "training_input_sha256": sha(input_path), "probe_reload_verified": True,
            "probe_predictions_sha256": pred_sha, "probe_reload_predictions_sha256": reload_sha,
            "artifacts": artifact_before_reload, "fit_predict_seconds": round(fit_seconds, 6),
            "training_rows": len(train), "probe_rows": len(probe)}
        atomic_json(receipt_path, receipt)
        return receipt
    except subprocess.TimeoutExpired as exc:
        # The owned worker tree has been terminated before recording timeout.
        receipt = {"schema": 1, "task_id": task.name, "status": "timeout", "source_sha256": source_sha,
            "training_input_sha256": sha(input_path), "probe_reload_verified": False,
            "error": f"training worker exceeded {budget} seconds"}
        atomic_json(receipt_path, receipt)
        return receipt
    except RuntimeError as exc:
        if not str(exc).startswith("candidate_worker_failed:"):
            raise
        receipt = {"schema": 1, "task_id": task.name, "status": "model_failure", "source_sha256": source_sha,
            "training_input_sha256": sha(input_path), "probe_reload_verified": False, "error": str(exc)}
        atomic_json(receipt_path, receipt)
        return receipt


def output_paths(workspace: Path) -> dict:
    paths = {"receipt": "training/receipt.json", "artifact_manifest": "training/artifacts/manifest.json",
        "evaluation": "output/evaluation/metrics.json", "model_info": "training/model_info.json"}
    return {name: {"path": "workspace/" + path, "path_base": "task", "required": name != "model_info"} for name, path in paths.items()}


def workspace_status(workspace: Path) -> dict:
    workspace = Path(workspace)
    return {name: {**row, "exists": (workspace.parent / row["path"]).is_file()} for name, row in output_paths(workspace).items()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)
    packet_path = Path(args.input).resolve()
    if args.status:
        print(json.dumps(workspace_status(packet_path.parent / "workspace"), ensure_ascii=False))
        return 0
    result = run_workspace(packet_path)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"success", "duplicate", "model_failure", "timeout"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
