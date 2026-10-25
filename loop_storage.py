"""Shared read-only research inputs; execution state remains in LoopController."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from loop_controller import atomic_json, read_json

COMMON_FIELDS = {"raw_sha256", "split_sha256", "folds_sha256", "packages", "stage",
                 "use", "comparable_to_current"}
HISTORY_FORMAT = "history-references-v1"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe(root, path):
    root = Path(root).resolve()
    path = Path(os.path.abspath(path))
    if path == root or not path.is_relative_to(root):
        raise ValueError("Input path escaped its allowed root")
    for part in (path, *path.parents):
        if part == root:
            break
        if (part.is_symlink() or getattr(part, "is_junction", lambda: False)()
                or (part.exists() and getattr(part.stat(), "st_file_attributes", 0) & 0x400)):
            raise ValueError("Research inputs cannot use links or reparse points")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Resolved input path escaped its allowed root")
    return path


def reference(task, path):
    return os.path.relpath(path, task).replace(os.sep, "/")


def input_path(task, value):
    """Only task-local files or flat, hash-bound text objects are readable."""
    task = Path(task).resolve()
    path = Path(os.path.abspath(task / value))
    if path.is_relative_to(task):
        return safe(task, path)
    objects = task.parent.parent / "inputs/objects"
    path = safe(objects, path)
    if path.parent != objects or not re.fullmatch(r"[0-9a-f]{64}\.txt", path.name):
        raise ValueError("Only immutable shared text objects may be referenced")
    if sha(path.read_bytes()) != path.stem:
        raise ValueError("Shared input content changed")
    return path


def store_bytes(runtime, data):
    root = Path(runtime).resolve()
    path = safe(root, root / "inputs/objects" / (sha(data) + ".txt"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("Existing shared input is corrupt; refusing overwrite")
        return path
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def store_file(task, source):
    source = Path(source)
    if source.suffix not in {".py", ".md", ".json", ".txt", ".toml"} and source.name != "uv.lock":
        raise ValueError("Shared research store accepts text inputs only")
    data = source.read_bytes()
    data.decode("utf-8")  # Lock/config references must remain text, not arbitrary binary inputs.
    return reference(task, store_bytes(Path(task).parent.parent, data))


def write_history(task, rows):
    """Append each distinct record once; task view freezes an array of row IDs."""
    task = Path(task).resolve()
    runtime = task.parent.parent
    catalog_path = safe(runtime, runtime / "inputs/history.json")
    catalog = read_json(catalog_path) if catalog_path.exists() else {
        "format": HISTORY_FORMAT, "groups": [], "records": []}
    if (catalog.get("format") != HISTORY_FORMAT or not isinstance(catalog.get("groups"), list)
            or not isinstance(catalog.get("records"), list)):
        raise ValueError("Invalid shared history catalog")
    groups = {canonical(group): i for i, group in enumerate(catalog["groups"])}
    records = {canonical(record): i for i, record in enumerate(catalog["records"])}
    changed, selected = False, []
    for row in rows:
        group = {k: v for k, v in row.items() if k in COMMON_FIELDS}
        key = canonical(group)
        if key not in groups:
            groups[key] = len(catalog["groups"])
            catalog["groups"].append(group)
            changed = True
        record = {"group": groups[key], "record": {k: v for k, v in row.items() if k not in COMMON_FIELDS}}
        key = canonical(record)
        if key not in records:
            records[key] = len(catalog["records"])
            catalog["records"].append(record)
            changed = True
        selected.append(records[key])
    if changed or not catalog_path.exists():
        atomic_json(catalog_path, catalog)
    view = {"format": HISTORY_FORMAT, "catalog": reference(task, catalog_path),
            "path_base": "task", "records": selected, "content_sha256": sha(canonical(rows))}
    atomic_json(task / "workspace/history/index.json", view)
    # Verify logical reconstruction before publishing C02.
    if read_history(task) != rows:
        raise ValueError("Shared history did not reconstruct the original records")


def read_history(task):
    task = Path(task).resolve()
    path = task / "workspace/history/index.json"
    if not path.exists():
        return []
    view = read_json(safe(task, path))
    if isinstance(view, list):
        return view  # Existing task snapshots retain their original byte hashes.
    if not isinstance(view, dict) or view.get("format") != HISTORY_FORMAT:
        raise ValueError("Invalid task history view")
    runtime = task.parent.parent
    catalog_path = safe(runtime, task / view["catalog"])
    if catalog_path != runtime / "inputs/history.json":
        raise ValueError("History catalog path is not permitted")
    catalog = read_json(catalog_path)
    if catalog.get("format") != HISTORY_FORMAT:
        raise ValueError("History catalog format changed")
    result = []
    for index in view["records"]:
        if type(index) is not int or not 0 <= index < len(catalog["records"]):
            raise ValueError("Invalid frozen history record reference")
        item = catalog["records"][index]
        group_index = item["group"]
        if type(group_index) is not int or not 0 <= group_index < len(catalog["groups"]):
            raise ValueError("Invalid history evaluation reference")
        group, record = catalog["groups"][group_index], item["record"]
        if set(group) & set(record):
            raise ValueError("History record overrides shared evaluation identity")
        result.append({**record, **group})
    if sha(canonical(result)) != view["content_sha256"]:
        raise ValueError("Frozen history content changed")
    return result


def readonly_paths(task, packet):
    paths = [Path(task) / "input.json", input_path(task, packet["task_file"]),
             *(input_path(task, p) for p in packet["knowledge_files"]),
             *(input_path(task, row["path"]) for row in packet["source_files"])]
    paths += [input_path(task, row["source_file"]) for row in read_history(task)]
    if packet.get("data"):
        paths += [input_path(task, packet["data"][key]) for key in ("train", "probe")]
    # Retain checks on older snapshots, including files not mapped by their index.
    paths += list((Path(task) / "workspace/history").rglob("*.py"))
    return list(dict.fromkeys(paths))


def verify_readonly(task, originals):
    for name, expected in originals.items():
        if sha(input_path(task, name).read_bytes()) != expected:
            raise ValueError("Read-only input changed: " + name)
    read_history(task)  # Catalog may grow; this task's selected records cannot change.
