"""File-based expert inputs and recoverable two-file publication, no scheduling."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import uuid

from loop_controller import atomic_json, read_json
from loop_storage import (canonical, input_path, read_history, safe,
                          sha, store_file, write_history)

CONTRACT = "expert-files-v1"


def file_sha(path):
    return sha(Path(path).read_bytes()) if Path(path).is_file() else None


def prepare(backend, runtime, every, root):
    runtime, root = Path(runtime).resolve(), Path(root).resolve()
    knowledge_path = runtime / "knowledge/accumulated.json"
    old = read_json(knowledge_path)
    from loop_contracts import runtime_view, freeze
    workflow = runtime_view(runtime)
    results = backend.results(runtime)
    successful = [(p, s, m) for p, s, m in results if m.get("status") == "success"
                  and m.get("reload_verified") is True and isinstance(m.get("roc_auc"), (int, float))
                  and math.isfinite(m["roc_auc"])]
    if not successful:
        raise ValueError("Expert requires a verified keeper")
    session = backend.session_settings("review")
    task = runtime / "tasks" / ("review-" + uuid.uuid4().hex[:16])
    workspace = task / "workspace"
    (workspace / "output/tmp").mkdir(parents=True)
    exposed, sources, history, parents, increments = [], [], [], [], []

    def expose(path):
        value = store_file(task, path)
        exposed.append(value)
        return value

    research_path = runtime / "knowledge/research.md"
    research_ref = expose(research_path) if research_path.is_file() else None
    config_ref = expose(knowledge_path)
    for metric_path, source, metric in results:
        local = expose(source)
        origin = source.resolve().relative_to(root).as_posix()
        history.append({**metric, "source_file": local, "canonical_source": origin,
                        "comparable_to_current": True})
        sources.append(dict(role="history", path=local))
        if any(source == s for _, s, _ in successful):
            if file_sha(metric_path) is None:
                raise ValueError("Historical parent has no formal metric file")
            parents.append(dict(parent_source=origin, path=local, sha256=file_sha(source),
                                metric_path=metric_path.resolve().relative_to(root).as_posix(), metric_sha256=file_sha(metric_path),
                                evaluation={k: metric.get(k) for k in ("raw_sha256", "split_sha256", "packages")}))
    for source, metric in backend.archived_results():
        history.append({**metric, "source_file": expose(source),
                        "canonical_source": source.resolve().relative_to(root).as_posix()})
    keeper = max(successful, key=lambda row: row[2]["roc_auc"])
    sources.append(dict(role="best", path=expose(keeper[1])))
    done = set()
    for delta in sorted((runtime / "tasks").glob("*/delta.json")):
        item = read_json(delta)
        identifier = item["task_id"]
        if identifier != delta.parent.name:
            raise ValueError("Delta identity does not match task directory")
        done.add(identifier)
        if identifier in old["review"]["covered_delta_ids"]:
            continue
        record = dict(task_id=identifier, delta=expose(delta),
                      feedback=expose(delta.parent / "feedback.json"))
        final = delta.parent / "workspace/output/final.txt"
        if final.is_file():
            record["final"] = expose(final)
        candidate = delta.parent / "workspace/train.py"
        if candidate.is_file():
            local = expose(candidate)
            record["source_file"] = local
            sources.append(dict(role="recent", path=local))
        increments.append(record)
    # Include all original explanations and feedback, even covered failures and
    # duplicates that never produced evaluator metrics.
    completed = []
    for feedback in sorted((runtime / "tasks").glob("*/feedback.json")):
        packet_path = feedback.parent / "input.json"
        if not packet_path.is_file() or read_json(packet_path).get("role") != "luna":
            continue
        row = dict(task_id=feedback.parent.name, feedback=expose(feedback))
        for key, name in (("final", "workspace/output/final.txt"), ("source_file", "workspace/train.py")):
            path = feedback.parent / name
            if path.is_file():
                row[key] = expose(path)
        completed.append(row)
    write_history(task, history)
    evidence = workspace / "history/feedback.json"
    atomic_json(evidence, dict(increments=increments, completed=completed))
    exposed.extend(["workspace/history/index.json", "workspace/history/feedback.json"])
    unfinished = workflow["plan"]["pending_tasks"] if workflow else [item for item in old["next_tasks"] if item["task_id"] not in done]
    catalog = sorted(set((root / "references").glob("*.md")) |
                     {root / name for name in backend.default_references if (root / name).is_file()})
    optional_files = {p.relative_to(root).as_posix(): expose(p) for p in catalog}
    contract = dict(protocol=CONTRACT, every=every, max_next_tasks=max(1, every),
                    research_file="workspace/output/research.md", plan_file="workspace/output/plan.json",
                    knowledge_before={"research.md": file_sha(research_path), "accumulated.json": file_sha(knowledge_path)},
                    knowledge_ref=research_ref, config_ref=config_ref, parents=parents,
                    increments=increments, unfinished_tasks=unfinished, optional_files=optional_files)
    text = ("你是周期性研究专家。读取当前知识、keeper、带评价身份的完整历史索引、原始说明与正式反馈。\n"
            "分析近期集中方向、只经过一次弱实现的分支、被过度概括的负结论及单模型/集成混杂比较。"
            "据证据决定继续当前分支或发展另一条路线；允许有理由的局部优化，不强制换家族或非keeper。\n"
            "仅使用research_io.files读取暴露的证据及编辑workspace/output/research.md和plan.json。"
            "不训练、不执行Python、不联网，不改控制器、评价器、分数或既有任务。\n"
            "research.md为下一阶段主知识正文，保持简洁并以历史为证据。plan.json为JSON对象，严格包含"
            "next_tasks、optional_files、covered_delta_ids。next_tasks可以为空，最多安排下一N轮；"
            "条目严格包含question、parent_source、evidence，不填写task_id。parent_source必须来自review.parents的"
            "规范路径或对应path；同评价身份的较弱历史候选可以作为起点。每项question须说明问题、相对父候选的"
            "实质区别、证据及停止/继续条件，不写无视逐轮结果的固定扫参表。evidence使用实际暴露的文件引用。\n"
            "optional_files使用review.optional_files的键；[]表示不附加可选资料。covered_delta_ids仅声明本次实际"
            "总结的review.increments.task_id；看得见不等于已消化。完整历史索引使用history-references-v1："
            "先读index，再按records中的下标合并catalog.groups与catalog.records记录。不同评价身份只供参考。\n"
            "review.unfinished_tasks中明确标记user_required=true或origin=user的任务必须保留。"
            "若与新计划冲突或超过下一N轮，报告冲突并停止交付，不静默删除。最终聊天只解释取舍，文件才是交付。\n"
            + json.dumps(dict(review=contract), ensure_ascii=False, indent=2))
    (workspace / "task.md").write_text(text, encoding="utf-8")
    packet = dict(task_id=task.name, role="review", task_file="workspace/task.md",
        source_files=sources, knowledge_files=list(dict.fromkeys(exposed)), data=None, training=None,
        research_inputs=None, review=contract, session=session,
        tool_space={"execution": {"protocol": "json-argv-v1", "server": "research_io",
                                  "shell_enabled": False, "tools": ["files"]}})
    binding = freeze(task, workflow)
    if binding:
        packet["workflow"] = binding
        packet["knowledge_files"].append(binding["file"])
        with (workspace / "task.md").open("a", encoding="utf-8") as stream:
            stream.write("\nC02.workflow冻结的JSON给出当前轮数、已完成/未消费计划、最新正式结果和未覆盖增量；"
                         "它们是程序状态，不从知识正文反推任务是否仍待执行。\n")
    atomic_json(task / "input.json", packet)
    return task


def _validated_plan(task, packet, old, root):
    contract = packet["review"]
    research = safe(task, task / contract["research_file"])
    proposal = read_json(safe(task, task / contract["plan_file"]))
    if not research.read_text(encoding="utf-8").strip():
        raise ValueError("Expert research.md is missing or empty")
    if not isinstance(proposal, dict) or set(proposal) != {"next_tasks", "optional_files", "covered_delta_ids"}:
        raise ValueError("Invalid expert plan fields")
    tasks, selected, covered = (proposal[key] for key in ("next_tasks", "optional_files", "covered_delta_ids"))
    if not isinstance(tasks, list) or len(tasks) > contract["max_next_tasks"]:
        raise ValueError("Expert plan exceeds the next review interval")
    parents = {}
    for parent in contract["parents"]:
        frozen = input_path(task, parent["path"])
        original = safe(root, root / parent["parent_source"])
        if file_sha(frozen) != parent["sha256"] or file_sha(original) != parent["sha256"]:
            raise ValueError("Exposed historical parent identity changed")
        if file_sha(safe(root, root / parent["metric_path"])) != parent["metric_sha256"]:
            raise ValueError("Historical parent evaluation identity changed")
        parents[parent["parent_source"]] = parents[parent["path"]] = parent["parent_source"]
    allowed_evidence = set(packet["knowledge_files"]) | {p["path"] for p in packet["source_files"]}
    allowed_evidence |= {row["source_file"] for row in read_history(task)}
    accepted = []
    for item in tasks:
        if (not isinstance(item, dict) or set(item) != {"question", "parent_source", "evidence"}
                or not isinstance(item["question"], str) or not item["question"].strip()
                or not isinstance(item["parent_source"], str) or item["parent_source"] not in parents
                or not isinstance(item["evidence"], list)
                or any(not isinstance(x, str) or x not in allowed_evidence for x in item["evidence"])):
            raise ValueError("Invalid expert question, parent or evidence path")
        accepted.append(dict(task_id="luna-" + uuid.uuid4().hex[:16], question=item["question"],
                             parent_source=parents[item["parent_source"]], evidence=item["evidence"]))
    required = [item for item in contract["unfinished_tasks"]
                if item.get("user_required") is True or item.get("origin") == "user"]
    retained = set()
    for item in required:
        match = next((x for x in accepted if id(x) not in retained and all(x[key] == item[key] for key in ("question", "parent_source", "evidence"))), None)
        if match is None:
            raise ValueError("Expert omitted or changed an unfinished user task")
        match.update(item)  # Preserve its original program ID and explicit user marker.
        retained.add(id(match))
    allowed_ids = {row["task_id"] for row in contract["increments"]}
    if not isinstance(covered, list) or any(not isinstance(x, str) or x not in allowed_ids for x in covered):
        raise ValueError("Unknown covered delta ID")
    if not isinstance(selected, list) or any(not isinstance(x, str) or x not in contract["optional_files"] for x in selected):
        raise ValueError("Optional reference is not exposed")
    if len({(item["question"], item["parent_source"]) for item in accepted}) != len(accepted):
        raise ValueError("Expert plan repeats the same question and parent")
    updated = {**old, "next_tasks": accepted, "optional_files": selected,
               "review": dict(task_id=task.name, covered_delta_ids=sorted(set(old["review"]["covered_delta_ids"]) | set(covered)))}
    return research, updated


def publish(task, root):
    """Finish the exact same journal; never allocate new IDs or overwrite edits."""
    task, root = Path(task).resolve(), Path(root).resolve()
    packet = read_json(task / "input.json")
    contract = packet["review"]
    runtime = task.parent.parent
    directory = task / "publication"
    journal = directory / "manifest.json"
    targets = {name: runtime / "knowledge" / name for name in ("research.md", "accumulated.json")}
    inputs = {name: file_sha(task / contract[key]) for name, key in (("research", "research_file"), ("plan", "plan_file"))}
    for parent in contract["parents"]:
        if (file_sha(input_path(task, parent["path"])) != parent["sha256"]
                or file_sha(safe(root, root / parent["parent_source"])) != parent["sha256"]):
            raise ValueError("Exposed historical parent identity changed")
        if file_sha(safe(root, root / parent["metric_path"])) != parent["metric_sha256"]:
            raise ValueError("Historical parent evaluation identity changed")
    if journal.exists():
        manifest = read_json(journal)
        if (manifest["task_id"] != task.name or manifest["input_sha256"] != file_sha(task / "input.json")
                or manifest["outputs"] != inputs or set(manifest["files"]) != set(targets)):
            raise ValueError("Review publication identity changed; preserve journal")
    else:
        if any(file_sha(path) != contract["knowledge_before"][name] for name, path in targets.items()):
            raise ValueError("Knowledge changed after review input was frozen")
        old = read_json(targets["accumulated.json"])
        research, updated = _validated_plan(task, packet, old, root)
        directory.mkdir(exist_ok=True)
        blobs = {"research.md": research.read_bytes(), "accumulated.json": canonical(updated) + b"\n"}
        files = {}
        for name, blob in blobs.items():
            staged = directory / name
            with staged.open("wb") as stream:
                stream.write(blob)
                stream.flush()
                os.fsync(stream.fileno())
            files[name] = dict(old=contract["knowledge_before"][name], new=sha(blob), target=str(targets[name]))
        manifest = dict(task_id=task.name, input_sha256=file_sha(task / "input.json"), outputs=inputs,
                        files=files, assigned_task_ids=[x["task_id"] for x in updated["next_tasks"]], status="prepared")
        atomic_json(journal, manifest)
    # Validate both before replacing either. Completed manifests never overwrite
    # a subsequent human edit, even when its bytes happen to equal the old version.
    staged = read_json(directory / "accumulated.json")
    if (manifest["assigned_task_ids"] != [x["task_id"] for x in staged["next_tasks"]]
            or staged["review"]["task_id"] != task.name):
        raise ValueError("Publication manifest disagrees with staged plan IDs")
    for name, target in targets.items():
        row = manifest["files"][name]
        if row["target"] != str(target) or file_sha(directory / name) != row["new"]:
            raise ValueError("Publication payload or destination changed")
        allowed = {row["new"]} if manifest["status"] == "complete" else {row["old"], row["new"]}
        if file_sha(target) not in allowed:
            raise ValueError("Knowledge was edited during publication; refusing overwrite")
    for name, target in targets.items():
        if file_sha(target) != manifest["files"][name]["new"]:
            temporary = target.with_name(target.name + ".review-tmp")
            with temporary.open("wb") as stream:
                stream.write((directory / name).read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
    manifest["status"] = "complete"
    atomic_json(journal, manifest)
    return True
