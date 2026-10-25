"""Export probabilities from a verified saved task; never fit."""
from __future__ import annotations
import argparse
import importlib.util
from pathlib import Path
import numpy as np
import pandas as pd
from loop_controller import read_json
from project_config import ROOT
from session_train import _validate_existing, artifact_identity, sha


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', type=Path, required=True, help='Task directory containing input.json')
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    task = args.task.resolve(); packet = read_json(task / 'input.json')
    receipt = _validate_existing(task, packet)
    if not receipt or receipt['status'] != 'success':
        raise ValueError('Prediction requires verified saved training')
    source = task / 'workspace/training/train.py'; artifacts = task / 'workspace/training/artifacts'
    output = args.output.resolve()
    if output.is_relative_to(task) or output == args.input.resolve():
        raise ValueError('Export outside immutable task evidence and input files')
    id_column = packet['data'].get('id')
    frame = pd.read_csv(args.input, dtype={id_column: str} if id_column else None)
    if packet['data']['target'] in frame:
        raise ValueError('Prediction input must not contain the target')
    if id_column and id_column not in frame:
        raise ValueError('Prediction input is missing configured ID column')
    ids = frame[id_column].copy() if id_column else None
    if ids is not None and (ids.isna().any() or ids.duplicated().any()):
        raise ValueError('Prediction IDs must be nonmissing and unique')
    features = frame.drop(columns=[id_column]) if id_column else frame
    spec = importlib.util.spec_from_file_location('saved_export_candidate', source)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    values = np.asarray(module.predict_saved(artifacts, features), dtype=float)
    if values.shape != (len(frame),) or not np.isfinite(values).all() or ((values<0)|(values>1)).any():
        raise ValueError('Saved candidate did not return one probability per input row')
    if sha(source) != receipt['source_sha256'] or artifact_identity(artifacts) != receipt['artifacts']:
        raise ValueError('Prediction modified saved source/artifacts')
    result = pd.DataFrame({'prediction': values})
    if ids is not None: result.insert(0, id_column, ids.to_numpy())
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists(): raise ValueError('Export exists; choose a new filename')
    result.to_csv(output, index=False)
    restored = pd.read_csv(output, dtype={id_column:str} if id_column else None)
    if len(restored)!=len(result) or (ids is not None and restored[id_column].tolist()!=ids.tolist()):
        raise ValueError('CSV round-trip changed row identity')
    print(output)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
