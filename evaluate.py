"""Independent saved-model evaluator for the fixed holdout."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from project_config import load_config, resolve
from session_train import artifact_identity, sha


def evaluate(project_root: Path, task: Path, output: Path) -> dict:
    project_root, task, output = Path(project_root).resolve(), Path(task).resolve(), Path(output).resolve()
    packet_path = task / "input.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    if packet.get("task_id") != task.name or packet.get("role") != "luna":
        raise ValueError("Eva task identity mismatch")
    config = load_config(project_root / "project.toml")
    from prepare import prepare
    prepare(project_root, verify_only=True)
    split = json.loads((project_root / "workspace/data/prepared/split.json").read_text(encoding="utf-8"))
    if split.get("source_sha256") != sha(resolve(config, config["data"]["train"])):
        raise ValueError("Source training data changed after split creation")
    holdout_path = project_root / "workspace/data/prepared/holdout.csv"
    research_partition = project_root / "workspace/data/prepared/research.csv"
    if (split.get("split_sha256") != packet["data"].get("split_sha256")
            or split.get("files", {}).get("holdout.csv") != sha(holdout_path)
            or split.get("files", {}).get("research.csv") != sha(research_partition)):
        raise ValueError("Frozen evaluation split changed")
    holdout = pd.read_csv(holdout_path)
    target = config["data"]["target"]
    id_column = config["data"].get("id")
    features = holdout.drop(columns=[c for c in (target, id_column) if c and c in holdout])
    training = task / "workspace/training"
    artifacts = training / "artifacts"
    receipt = json.loads((training / "receipt.json").read_text(encoding="utf-8"))
    if receipt.get("status") != "success" or receipt.get("training_input_sha256") != sha(packet_path):
        raise ValueError("Eva requires successful training for this frozen C02")
    actual_artifacts = artifact_identity(artifacts)
    if actual_artifacts != receipt.get("artifacts"):
        raise ValueError("Saved artifact bytes do not match the training receipt")
    source_path = task / "workspace/train.py"
    source_sha = sha(source_path)
    if source_sha != receipt.get("source_sha256") or sha(training / "train.py") != source_sha:
        raise ValueError("Candidate source does not match its training receipt")
    spec = importlib.util.spec_from_file_location("simple_loop_eval_candidate", source_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    if not callable(getattr(module, "predict_saved", None)):
        raise ValueError("Candidate has no saved prediction entry")
    predictions = np.asarray(module.predict_saved(artifacts, features), dtype=float)
    if predictions.shape != (len(holdout),) or not np.isfinite(predictions).all():
        raise ValueError("Saved model produced invalid holdout predictions")
    score = float(roc_auc_score(holdout[target], predictions))
    probe_predictions = np.load(training / "fit_predictions.npy", allow_pickle=False)
    probe_frame = pd.read_csv(task / packet["data"]["probe"])
    probe_features = probe_frame.drop(columns=[c for c in (target, id_column) if c and c in probe_frame])
    reload_predictions = np.asarray(module.predict_saved(artifacts, probe_features), dtype=float)
    if reload_predictions.shape != probe_predictions.shape or not np.allclose(
            probe_predictions, reload_predictions, rtol=1e-9, atol=1e-11):
        raise ValueError("Independent Eva process did not reproduce the training probe")
    # Eva binds actual predictions and artifact bytes to the receipt.
    probe_sha = hashlib.sha256(probe_predictions.tobytes()).hexdigest()
    reload_sha = hashlib.sha256(reload_predictions.tobytes()).hexdigest()
    if probe_sha != receipt.get("probe_predictions_sha256") or reload_sha != receipt.get("probe_reload_predictions_sha256"):
        raise ValueError("Eva probe hashes disagree with the training receipt")
    if artifact_identity(artifacts) != actual_artifacts or sha(source_path) != source_sha:
        raise ValueError("Candidate prediction changed its source or saved artifacts")
    result = {"run_id": task.name, "status": "success", "roc_auc": score,
        "hypothesis": (packet.get("provenance") or {}).get("question", "baseline"),
        "reload_verified": True, "training_status": "success", "training_source_sha256": source_sha,
        "train_sha256": source_sha, "training_input_sha256": sha(packet_path),
        "probe_predictions_sha256": probe_sha, "probe_reload_predictions_sha256": reload_sha,
        "artifacts": {"sha256": actual_artifacts}, "split_sha256": split["split_sha256"],
        "holdout_rows": len(holdout)}
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, output / "train.py")
    if sha(output / "train.py") != source_sha:
        raise ValueError("Eva source copy failed identity verification")
    np.save(output / "predictions.npy", predictions, allow_pickle=False)
    result["predictions_sha256"] = sha(output / "predictions.npy")
    from loop_controller import atomic_json
    atomic_json(output / "metrics.json", result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(evaluate(args.project_root, args.task, args.output), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
