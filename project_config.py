"""Project-local configuration and path resolution for Simple Loop."""
from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "project.toml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    path = Path(path).resolve()
    with path.open("rb") as stream:
        config = tomllib.load(stream)
    project = config.get("project", {})
    data = config.get("data", {})
    evaluation = config.get("evaluation", {})
    for key in ("name",):
        if not isinstance(project.get(key), str) or not project[key].strip():
            raise ValueError(f"project.{key} must be a nonempty string")
    for key in ("train", "target"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"data.{key} must be a nonempty string")
    if not isinstance(data.get("id", "id"), str):
        raise ValueError("data.id must be a string")
    fraction = evaluation.get("holdout_fraction", 0.2)
    seed = evaluation.get("seed", 42)
    if not isinstance(fraction, (int, float)) or not 0 < fraction < 0.5:
        raise ValueError("evaluation.holdout_fraction must be between 0 and 0.5")
    if type(seed) is not int:
        raise ValueError("evaluation.seed must be an integer")
    if evaluation.get("metric", "roc_auc") != "roc_auc":
        raise ValueError("This release supports the binary roc_auc metric only")
    if evaluation.get("metric", "roc_auc") != "roc_auc":
        raise ValueError("This release supports roc_auc only")
    config["_root"] = path.parent
    return config


def resolve(config: dict, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (config["_root"] / path).resolve()
