"""The sole controller for the small C01/C02/C03 research loop.

Stop requests are in-memory events. Only this worker writes control.json; task
inputs/results and scientific knowledge are independent files, not checkpoints.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
DEFAULT_RUNTIME = ROOT / "workspace/runtime"
STATE_KEYS = {"target_rounds", "current_round", "review_every", "next_step", "active_task", "status"}
OPTIONAL_STATE_KEYS = {"review_cycle_start", "models"}
TERMINAL = {"success", "duplicate", "model_failed"}


class ControlIntegrityError(RuntimeError):
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def writer_lock(path):
    """A kernel lock, not a stale PID or a second state document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if not path.stat().st_size:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("已有控制器运行；请使用原窗口。") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def validate_state(state):
    if not STATE_KEYS <= set(state) or set(state) - STATE_KEYS - OPTIONAL_STATE_KEYS:
        raise ValueError("C01 contains unknown or missing fields.")
    for key in ("current_round", "review_every"):
        if type(state[key]) is not int or state[key] < 0:
            raise ValueError(f"Invalid C01 {key}")
    target = state["target_rounds"]
    if target is not None and (type(target) is not int or target < 1):
        raise ValueError("目标轮数应为正整数或空值。")
    if state["next_step"] not in {"luna", "evaluate", "review"}:
        raise ValueError("Invalid C01 next_step")
    if state["status"] not in {"ready", "running", "stopping", "stopped", "blocked", "completed"}:
        raise ValueError("Invalid C01 status")
    task = state["active_task"]
    if task is not None and (not isinstance(task, str) or not task or
                             any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in task)):
        raise ValueError("Invalid C01 active_task")
    if state["next_step"] == "evaluate" and task is None:
        raise ValueError("评分分支必须保留原任务。")
    start = state.get("review_cycle_start")
    if start is not None and (type(start) is not int or not 0 <= start <= state["current_round"]):
        raise ValueError("Invalid review cycle origin")
    models = state.get("models", {})
    if not isinstance(models, dict) or set(models) - {"luna", "review"}:
        raise ValueError("Invalid model settings")
    for config in models.values():
        if (not isinstance(config, dict) or set(config) - {"model", "effort"}
                or any(not isinstance(v, str) or not v.strip() for v in config.values())
                or config.get("effort", "high") not in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}):
            raise ValueError("Invalid role model or reasoning effort")
    return state


class LoopController:
    def __init__(self, runtime=DEFAULT_RUNTIME, on_event=None, *, backend=None):
        self.runtime = Path(runtime).resolve()
        self.on_event = on_event or (lambda event: None)
        self.backend = backend
        self._stop = threading.Event()
        self._running = threading.Lock()
        self.state = None
        self._saved_digest = None

    def read_state(self):
        path = self.runtime / "control.json"
        if path.exists():
            return validate_state(read_json(path))
        from project_config import load_config
        review_every = 0
        return dict(target_rounds=10, current_round=0, review_every=review_every,
                    next_step="luna", active_task=None, status="stopped")

    def _emit(self, event):
        # Rendering failures must not interrupt accepted scientific results.
        try:
            self.on_event(event)
        except Exception:
            pass

    def log(self, message):
        line = f"{datetime.now(timezone.utc).astimezone():%H:%M:%S} {message}"
        self.runtime.mkdir(parents=True, exist_ok=True)
        with (self.runtime / "console.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
        self._emit({"type": "log", "message": line})

    def _save(self, **changes):
        self._verify_control()
        updated = validate_state({**self.state, **changes})
        atomic_json(self.runtime / "control.json", updated)
        self._saved_digest = hashlib.sha256((self.runtime / "control.json").read_bytes()).hexdigest()
        self.state = updated
        self._emit({"type": "state", "state": dict(updated)})

    def request_stop(self):
        """Finish the current round (including Eva); never kill its process."""
        self._stop.set()

    def _verify_control(self):
        if self._saved_digest is not None:
            path = self.runtime / "control.json"
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != self._saved_digest:
                raise ControlIntegrityError("C01 在控制器之外被修改；保留观察到的文件，不覆盖回滚。")

    def _pulse(self):
        self._verify_control()
        if self._stop.is_set() and self.state["status"] == "running":
            self._save(status="stopping")
            self.log("已收到停止请求；完成当前轮并保存后停止。")

    def _task(self):
        return self.runtime / "tasks" / self.state["active_task"]

    def configure(self, *, review_every=None, models=None):
        """Explicit, idle-only configuration. This entry never dispatches work."""
        if not self._running.acquire(blocking=False):
            raise RuntimeError("Controller is running")
        try:
            with writer_lock(ROOT / "workspace/writer.lock"):
                self.state = self.read_state()
                control = self.runtime / "control.json"
                self._saved_digest = hashlib.sha256(control.read_bytes()).hexdigest() if control.exists() else None
                if self.state["active_task"] or self.state["status"] in {"running", "stopping"}:
                    raise ValueError("Configuration requires an idle safe boundary")
                changes = {}
                if models is not None:
                    merged = {k: dict(v) for k, v in self.state.get("models", {}).items()}
                    for role, config in models.items():
                        merged[role] = {**merged.get(role, {}), **config}
                    changes["models"] = merged
                if review_every is not None:
                    if type(review_every) is not int or review_every < 0:
                        raise ValueError("review_every must be a nonnegative integer")
                    from project_config import load_config
                    configured_expert = load_config()["evaluation"].get("expert_model")
                    if (review_every and not changes.get("models", self.state.get("models", {}))
                            .get("review", {}).get("model") and not configured_expert):
                        raise ValueError("Configure the expert model explicitly before enabling review")
                    if review_every != self.state["review_every"] or self.state.get("review_cycle_start") is None:
                        changes.update(review_every=review_every, review_cycle_start=self.state["current_round"] if review_every else None,
                                       next_step="luna")
                self._save(**changes)
                return dict(self.state)
        finally:
            self._running.release()

    def _review_due(self, count=None):
        start = self.state.get("review_cycle_start")
        return (bool(self.state["review_every"]) and start is not None
                and (self.state["current_round"] if count is None else count) - start >= self.state["review_every"])

    def _accept(self, feedback, *, recovering=False):
        task = self._task()
        packet = read_json(task / "input.json")
        from loop_contracts import feedback_matches
        feedback_matches(packet, feedback)
        if read_json(task / "feedback.json") != feedback:
            raise ValueError("C03 argument disagrees with saved formal feedback")
        if (set(feedback) != {"task_id", "status", "score", "artifacts", "error"}
                or feedback["task_id"] != self.state["active_task"]
                or packet["task_id"] != feedback["task_id"] or feedback["status"] not in TERMINAL):
            raise ValueError("C03 与当前任务不符，拒绝推进。")
        role = packet["role"]
        if hasattr(self.backend, "validate_feedback"):
            self.backend.validate_feedback(task, feedback)
        if role == "review" and feedback["status"] != "success":
            self._save(status="blocked")
            self.log("旧专家任务未成功；保留其状态并阻塞，不能自动重试。")
            return False
        if role == "review" and (packet.get("review") or {}).get("protocol") == "expert-files-v1":
            if not self.backend.returned(task):
                raise ValueError("Review receipt has no verified returned session")
            # Also required when C03 already existed before a crash: do not accept
            # a completed publication whose files were edited before settlement.
            self.backend.publish(task)
        count = self.state["current_round"]
        if packet.get("workflow"):
            from loop_contracts import VIEW, digest
            binding = packet["workflow"]
            if binding["file"] != VIEW or digest(task / VIEW) != binding["sha256"]:
                raise ValueError("Frozen settlement JSON changed")
            prepared = read_json(task / VIEW)
            if prepared["control"]["current_round"] != count:
                raise ValueError("Settlement round disagrees with its prepared C01")
        if role == "luna":
            from loop_contracts import validate_dispatch
            validate_dispatch(task, packet, allow_legacy_receive=True)
            # Idempotent local receive. A crash here keeps active_task and count.
            delta = self.backend.delta(task, feedback)
            if delta.get("task_id") != task.name or delta.get("feedback_ref") != "feedback.json":
                raise ValueError("Delta disagrees with the current C02/C03")
            if packet.get("workflow"):
                delta["settlement"] = dict(research_round=count + 1,
                    input_sha256=digest(task / "input.json"), feedback_sha256=digest(task / "feedback.json"))
            if (task / "delta.json").is_file():
                if read_json(task / "delta.json") != delta:
                    raise ValueError("Existing delta disagrees with this settlement; preserving original")
            else:
                atomic_json(task / "delta.json", delta)
            count += 1
        elif role != "review":
            raise ValueError("Unknown C02 role")
        next_step = "review" if role == "luna" and self._review_due(count) else "luna"
        stopped = recovering or self._stop.is_set() or self.state["status"] in {"stopping", "stopped", "blocked"}
        complete = self.state["target_rounds"] is not None and count >= self.state["target_rounds"]
        changes = {"review_cycle_start": count} if role == "review" and self.state.get("review_cycle_start") is not None else {}
        self._save(current_round=count, next_step=next_step, active_task=None, **changes,
                   status="stopped" if stopped else "completed" if complete else "ready")
        self.log(f"已接收 {role} {feedback['task_id']}：{feedback['status']}；Luna 已完成 {count} 轮。")
        return True

    def run(self, mode="continue", target_rounds=5, review_every=None):
        if mode not in {"new", "continue", "review"}:
            raise ValueError("Unknown entry mode")
        validate_state(dict(target_rounds=target_rounds, current_round=0, review_every=review_every or 0,
                            next_step="luna", active_task=None, status="ready"))
        if not self._running.acquire(blocking=False):
            raise RuntimeError("当前窗口已有任务运行。")
        try:
            with writer_lock(ROOT / "workspace/writer.lock"):
                self.state = self.read_state()
                control = self.runtime / "control.json"
                self._saved_digest = hashlib.sha256(control.read_bytes()).hexdigest() if control.exists() else None
                if self.backend is None:
                    from loop_backend import ResearchBackend
                    self.backend = ResearchBackend()
                try:
                    if hasattr(self.backend, "configure"):
                        self.backend.configure(self.state.get("models", {}))
                    self._run(mode, target_rounds, review_every)
                except ControlIntegrityError as exc:
                    # Retain observed bytes, including corruption. The caller receives
                    # the blocked in-memory result; no old checkpoint is restored.
                    self.state = {**self.state, "status": "blocked"}
                    self._emit({"type": "state", "state": dict(self.state)})
                    self.log(str(exc))
                except Exception as exc:
                    # Never reset progress, discard receipts, or replay an unknown call.
                    self._save(status="blocked")
                    self.log(f"已阻塞：{type(exc).__name__}: {exc}")
                return dict(self.state)
        finally:
            self._stop.clear()
            self._running.release()

    def _run(self, mode, target, every):
        if hasattr(self.backend, "validate_runtime"):
            self.backend.validate_runtime(self.runtime)
        if every is not None and self.state["active_task"] and every != self.state["review_every"]:
            raise ValueError("Cannot change review cadence with an active task")
        active = self.state["active_task"]
        if active:
            task = self._task()
            feedback_path = task / "feedback.json"
            packet = read_json(task / "input.json")
            if packet.get("role") == "review" and not feedback_path.exists() and not self.backend.returned(task):
                raise RuntimeError("旧专家任务尚无已返回结果；保留现场并阻塞，不清空、重放或跳过。")
            else:
                if mode == "new":
                    raise ValueError("原任务尚未接收，不能新建目标。请先继续处理原任务。")
                if not feedback_path.exists():
                    if self.state["next_step"] != "evaluate" and not self.backend.returned(task):
                        if not getattr(self.backend, "not_started", lambda task: False)(task):
                            raise RuntimeError("原模型调用结果未知；保留现场，不自动重发。")
                        self.backend.preflight(task)
                        if self._stop.is_set():
                            self._save(status="stopped")
                            return
                        self.log("原进程明确未能创建；按本次继续命令启动原任务。")
                        self._save(status="running")
                        self.backend.invoke(task, self._pulse, self.log)
                        if read_json(task / "input.json")["role"] == "luna":
                            self._save(next_step="evaluate")
                    self.log("仅接收原任务的已保存交付；不会重新调用模型或训练。")
                    feedback = self.backend.finish(task, self._pulse, self.log)
                else:
                    feedback = read_json(feedback_path)
                self._accept(feedback, recovering=self.state["status"] in {"blocked", "running", "stopping"})
                if mode == "review":
                    if packet["role"] != "review":
                        raise ValueError("Received original research task; run review again explicitly at idle boundary")
                    if self.state["status"] != "blocked":
                        self._save(status="stopped")
                    return
                if self.state["status"] != "ready":
                    return
        if mode == "new":
            if self.state["current_round"]:
                raise ValueError("Use continue; existing research count cannot be reset")
            enabled = (every if every is not None else self.state["review_every"]
                       if self.state.get("review_cycle_start") is not None else 0)
            self._save(target_rounds=target, review_every=enabled,
                       review_cycle_start=0 if enabled else None,
                       next_step="luna", active_task=None, status="ready")
        else:
            if target is not None and target < self.state["current_round"]:
                raise ValueError("目标轮数不能少于已经完成的轮数。")
            changes = {}
            if every is not None and (every != self.state["review_every"] or self.state.get("review_cycle_start") is None):
                changes.update(review_every=every, review_cycle_start=self.state["current_round"] if every else None)
            due = self._review_due() if not changes else False
            self._save(target_rounds=target, status="ready", **changes,
                       next_step="review" if mode == "review" or due else "luna")
        one_review = mode == "review"
        if (one_review or (self.state["review_every"] and self.state.get("review_cycle_start") is not None)) and hasattr(self.backend, "session_settings"):
            self.backend.session_settings("review")  # Reject missing expert settings before spending research slots.
        while self.state["status"] == "ready":
            if self._stop.is_set():
                self._save(status="stopped")
                break
            if (not one_review and self.state["target_rounds"] is not None
                    and self.state["current_round"] >= self.state["target_rounds"]):
                self._save(status="completed")
                break
            self.backend.initialize(self.runtime)
            role = "review" if self.state["next_step"] == "review" else "luna"
            self.log("准备专家审核输入。" if role == "review" else "准备 Luna 输入。")
            task = self.backend.prepare(self.runtime, role, self.state["review_every"])
            # Bootstrap/permissions checks occur before recording a charged dispatch.
            self.backend.preflight(task)
            if self._stop.is_set():
                self._save(status="stopped")
                break
            self._save(active_task=task.name, status="running", next_step="luna")
            self.backend.invoke(task, self._pulse, self.log)
            self._pulse()
            if role == "luna":
                self._save(next_step="evaluate")
                self.log("Luna 已返回；接收训练产物并由 Eva 评分。")
            feedback = self.backend.finish(task, self._pulse, self.log)
            self._pulse()
            self._accept(feedback)
            if one_review and self.state["status"] != "blocked":
                self._save(status="stopped")
        self.log(f"控制器结束：{self.state['status']}。")
