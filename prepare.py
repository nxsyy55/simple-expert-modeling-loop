"""Prepare and verify a fixed binary-classification holdout once."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

from project_config import load_config, resolve


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(root: Path, *, verify_only: bool = False) -> dict:
    root = Path(root).resolve()
    config = load_config(root / "project.toml")
    out = root / "workspace/data/prepared"
    manifest_path = out / "split.json"
    research_path, holdout_path = out / "research.csv", out / "holdout.csv"
    if manifest_path.exists() or research_path.exists() or holdout_path.exists():
        if not (manifest_path.is_file() and research_path.is_file() and holdout_path.is_file()):
            raise ValueError("Partial prepared split exists; preserve it and resolve manually")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        saved_identity = manifest.get("split_sha256")
        unhashed = {k: v for k, v in manifest.items() if k != "split_sha256"}
        calculated = hashlib.sha256(json.dumps(unhashed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if saved_identity != calculated:
            raise ValueError("Prepared split manifest identity changed")
        expected = manifest.get("files", {})
        actual = {"research.csv": digest(research_path), "holdout.csv": digest(holdout_path)}
        if expected != actual:
            raise ValueError("Prepared split content changed; refusing to replace this experiment domain")
        if manifest.get("source_sha256") != digest(resolve(config, config["data"]["train"])):
            raise ValueError("Original training data changed after split creation")
        expected_config = {"target": config["data"]["target"], "id": config["data"].get("id"),
                           "metric": config["evaluation"].get("metric", "roc_auc"),
                           "seed": config["evaluation"].get("seed", 42),
                           "holdout_fraction": config["evaluation"].get("holdout_fraction", .2),
                           "evaluator_sha256": digest(root / "evaluate.py"), "lock_sha256": digest(root / "uv.lock")}
        if any(manifest.get(k) != v for k, v in expected_config.items()):
            raise ValueError("Scientific configuration changed after split creation")
        identity = {k:v for k,v in manifest.items() if k != "split_sha256"}
        if hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest() != manifest["split_sha256"]:
            raise ValueError("Split manifest identity changed")
        return manifest
    if verify_only:
        raise FileNotFoundError("Prepared split does not exist")
    source = resolve(config, config["data"]["train"])
    frame = pd.read_csv(source)
    target = config["data"]["target"]
    if target not in frame or frame[target].isna().any() or frame[target].nunique() != 2:
        raise ValueError("Training CSV must have a nonmissing binary target")
    train_indices, holdout_indices = train_test_split(
        list(range(len(frame))), test_size=config["evaluation"].get("holdout_fraction", 0.2),
        random_state=config["evaluation"].get("seed", 42), stratify=frame[target])
    out.mkdir(parents=True, exist_ok=True)
    frame.iloc[train_indices].to_csv(research_path, index=False)
    frame.iloc[holdout_indices].to_csv(holdout_path, index=False)
    id_column = config["data"].get("id")
    identities = {}
    for name, indices in (("research", train_indices), ("holdout", holdout_indices)):
        values = (frame.iloc[indices][id_column].astype(str).tolist() if id_column and id_column in frame
                  else [str(index) for index in indices])
        identities[name + "_row_ids_sha256"] = hashlib.sha256("\n".join(values).encode()).hexdigest()
    manifest = {"schema": 1, "evaluator_sha256": digest(root / "evaluate.py"), "lock_sha256": digest(root / "uv.lock"), "source_sha256": digest(source), "target": target,
        "id": config["data"].get("id"), "holdout_fraction": config["evaluation"].get("holdout_fraction", .2),
        "metric": config["evaluation"].get("metric", "roc_auc"), "seed": config["evaluation"].get("seed", 42),
        "research_rows": len(train_indices), "holdout_rows": len(holdout_indices),
        **identities, "files": {"research.csv": digest(research_path), "holdout.csv": digest(holdout_path)}}
    manifest["split_sha256"] = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(prepare(args.project_root, verify_only=args.verify_only), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
