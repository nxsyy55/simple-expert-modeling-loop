"""One isolated candidate operation used by the session supervisor."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_candidate(source, expected_sha):
    if digest(source) != expected_sha:
        raise ValueError("Candidate source changed before worker start")
    spec = importlib.util.spec_from_file_location("simple_loop_candidate_worker", source)
    module = importlib.util.module_from_spec(spec)
    if not spec or not spec.loader:
        raise ImportError("Cannot load candidate")
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args(argv)
    request = json.loads(args.request.read_text(encoding="utf-8"))
    module = load_candidate(Path(request["source"]), request["source_sha256"])
    model_dir = Path(request["model_dir"])
    frame = pd.read_csv(request["predict_csv"])
    if request["operation"] == "fit":
        train = pd.read_csv(request["train_csv"])
        target = request["target"]
        excluded = {target, request.get("id")}
        features = train.drop(columns=[x for x in excluded if x and x in train])
        predict_features = frame.drop(columns=[x for x in excluded if x and x in frame])
        values = np.asarray(module.fit_predict(features, train[target], predict_features, model_dir), dtype=float)
    elif request["operation"] == "predict":
        features = frame.drop(columns=[x for x in (request.get("target"), request.get("id")) if x and x in frame])
        values = np.asarray(module.predict_saved(model_dir, features), dtype=float)
    else:
        raise ValueError("Unknown candidate worker operation")
    if values.shape != (len(frame),) or not np.isfinite(values).all():
        raise ValueError("Candidate returned invalid predictions")
    np.save(request["output_npy"], values, allow_pickle=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
