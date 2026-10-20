"""Persistent CAM tasks. A plan or a camera observation can never mark CAM complete."""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import uuid
from pathlib import Path

from .camera import utc_now
from .config import Settings


STAGES = ("inputs_checked", "project_created", "model_imported", "stock_configured",
          "tools_configured", "operations_configured", "toolpaths_generated",
          "simulation_reviewed", "project_saved", "nc_exported", "delivery_checked")


def file_identity(path: str) -> dict:
    target = Path(path).resolve(strict=True)
    if not target.is_file() or target.stat().st_size == 0:
        raise ValueError("Expected a nonempty input file")
    if target.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("Input exceeds 256 MiB")
    return {"path": str(target), "bytes": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def validate_spec(spec: dict) -> tuple[dict, list[str]]:
    missing = list(spec.get("unresolved_inputs", []))
    if not all(isinstance(item, str) for item in missing):
        raise ValueError("unresolved_inputs must be a list of strings")
    required_missing = []
    for key in ("model_path", "stock_mm", "material", "work_origin", "tools", "operations", "output_directory"):
        if not spec.get(key):
            required_missing.append(key)
    if required_missing:
        return spec, sorted(set(missing + required_missing))
    model = file_identity(spec["model_path"])
    if Path(model["path"]).suffix.lower() not in (".step", ".stp", ".stl", ".svg", ".dxf", ".obj"):
        raise ValueError("Unsupported model type")
    if spec.get("units", "mm") != "mm":
        raise ValueError("Task dimensions currently require explicit mm units")
    stock = spec["stock_mm"]
    for axis in ("x", "y", "z"):
        value = stock.get(axis)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
            raise ValueError("stock_mm x/y/z must be finite positive numbers")
    if not isinstance(spec["material"], str) or not spec["material"].strip():
        raise ValueError("material must be a nonempty name")
    if not isinstance(spec["work_origin"], str) or not spec["work_origin"].strip():
        raise ValueError("work_origin must explain the selected stock/model reference")
    tool_ids = set()
    if not isinstance(spec["tools"], list) or not isinstance(spec["operations"], list):
        raise ValueError("tools and operations must be lists")
    for tool in spec["tools"]:
        if not isinstance(tool, dict):
            raise ValueError("Each tool must be an object")
        for key in ("id", "type", "diameter_mm", "shank_mm"):
            if key not in tool:
                missing.append("tools[]." + key)
        if "id" in tool:
            if not isinstance(tool["id"], str) or not tool["id"] or tool["id"] in tool_ids:
                raise ValueError("Tool IDs must be unique nonempty strings")
            tool_ids.add(tool["id"])
        for key in ("diameter_mm", "shank_mm"):
            if key in tool and (isinstance(tool[key], bool) or not isinstance(tool[key], (float, int))
                                or not math.isfinite(tool[key]) or tool[key] <= 0):
                raise ValueError("Tool dimensions must be finite positive numbers")
    for operation in spec["operations"]:
        if not isinstance(operation, dict):
            raise ValueError("Each operation must be an object")
        for key in ("type", "tool_id", "depth_mm", "region"):
            if key not in operation or operation[key] is None:
                missing.append("operations[]." + key)
        if operation.get("tool_id") not in tool_ids:
            raise ValueError("Operation must reference a tool in this task")
        if operation.get("depth_mm") is not None:
            depth = operation["depth_mm"]
            if isinstance(depth, bool) or not isinstance(depth, (float, int)) or not math.isfinite(depth) or depth <= 0:
                raise ValueError("Depth must be finite and positive")
            if depth > stock["z"] and not spec.get("through_cut_allowance_mm"):
                raise ValueError("Depth exceeds stock; explicit through-cut allowance is missing")
        for key in ("rpm", "feed_mm_min", "stepdown_mm"):
            if key not in operation or operation[key] is None:
                missing.append("operations[]." + key)
            elif (isinstance(operation[key], bool) or not isinstance(operation[key], (float, int))
                  or not math.isfinite(operation[key]) or operation[key] <= 0):
                raise ValueError("Cutting parameters must be finite positive numbers")
    normalized = {**spec, "units": "mm", "input_model": model,
                  "output_directory": str(Path(spec["output_directory"]).resolve()),
                  "execution_scope": "studio_cam_only"}
    return normalized, sorted(set(missing))


class TaskStore:
    def __init__(self, settings: Settings):
        self.path = settings.runtime / "tasks"
        self.path.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def _path(self, task_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", task_id):
            raise ValueError("Invalid task ID")
        return self.path / (task_id + ".json")

    def _save(self, task: dict):
        destination = self._path(task["id"])
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination)

    def create(self, spec: dict) -> dict:
        normalized, missing = validate_spec(spec)
        task = {"id": uuid.uuid4().hex, "created_at": utc_now(), "updated_at": utc_now(),
                "revision": 1, "spec": normalized, "missing_inputs": missing,
                "status": "needs_inputs" if missing else "ready_for_desktop_execution",
                "stages": {stage: {"status": "pending"} for stage in STAGES},
                "video_role": "optional_observation_only", "machine_execution_authorized": False,
                "events": []}
        if not normalized.get("deliver_nc", True):
            task["stages"]["nc_exported"] = {"status": "not_requested"}
        if not missing:
            task["stages"]["inputs_checked"] = {"status": "complete", "at": utc_now(),
                                                     "input_sha256": normalized["input_model"]["sha256"]}
        with self.lock:
            self._save(task)
        return task

    def get(self, task_id: str) -> dict:
        with self.lock:
            return json.loads(self._path(task_id).read_text(encoding="utf-8"))

    def list(self) -> dict:
        with self.lock:
            result = []
            for path in sorted(self.path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
                data = json.loads(path.read_text(encoding="utf-8"))
                result.append({key: data[key] for key in ("id", "status", "revision", "updated_at")})
            return {"tasks": result}

    def next_action(self, task_id: str) -> dict:
        task = self.get(task_id)
        if task["missing_inputs"]:
            return {"status": "needs_inputs", "missing_inputs": task["missing_inputs"]}
        current = file_identity(task["spec"]["model_path"])
        if current["sha256"] != task["spec"]["input_model"]["sha256"]:
            return {"status": "input_changed", "required_action": "revise_task_before_execution"}
        stage = next((name for name in STAGES if task["stages"][name]["status"] not in ("complete", "not_requested")), None)
        return {"status": task["status"], "stage": stage,
                "driver": "standalone_native_qt_mcp",
                "required_action": "Use verified native Studio MCP tools for this stage; toolpath and simulation adapters are still under development",
                "requirements": task["spec"],
                "completion_policy": "Video samples, tool discovery and queued actions cannot complete any CAM stage"}

    def revise(self, task_id: str, spec: dict) -> dict:
        normalized, missing = validate_spec(spec)
        with self.lock:
            task = self.get(task_id)
            task.update(spec=normalized, missing_inputs=missing, revision=task["revision"] + 1,
                        updated_at=utc_now(), status="needs_inputs" if missing else "ready_for_desktop_execution")
            task["stages"] = {stage: {"status": "invalidated"} for stage in STAGES}
            if not normalized.get("deliver_nc", True):
                task["stages"]["nc_exported"] = {"status": "not_requested"}
            if not missing:
                task["stages"]["inputs_checked"] = {"status": "complete", "at": utc_now(),
                                                         "input_sha256": normalized["input_model"]["sha256"]}
            task["events"].append({"at": utc_now(), "event": "parameters_revised_all_downstream_results_invalidated"})
            self._save(task)
            return task
