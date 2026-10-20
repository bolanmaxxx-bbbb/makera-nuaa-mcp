"""Build reviewed MCP arguments and audit routing. Never invokes tools or moves hardware."""
import argparse
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]


def unwrap(value):
    if isinstance(value, dict) and "content" in value:
        if value.get("isError"):
            raise ValueError("MCP returned an error, not usable evidence")
        if value.get("structuredContent"):
            return value["structuredContent"]
        for item in value["content"]:
            if item.get("type") == "text":
                return json.loads(item["text"])
    return value


def audit(capabilities, tool_list):
    capabilities, tool_list = unwrap(capabilities), unwrap(tool_list)
    entries = tool_list.get("tools", []) if isinstance(tool_list, dict) else tool_list
    names = {item if isinstance(item, str) else item["name"] for item in entries}
    names = {name.split("__")[-1] for name in names}
    catalog = json.loads((SKILL / "assets/mcp-tool-map.json").read_text(encoding="utf-8"))
    supported = set(capabilities.get("supported", []))
    results = {}
    for tool in catalog["tools"]:
        missing = sorted(set(tool["requires"]) - supported)
        results[tool["name"]] = {"available": tool["name"] in names and not missing,
            "exposed": tool["name"] in names, "missing_capabilities": missing,
            "route": tool["route"], "reference": tool["reference"]}
    unmapped = sorted(names - results.keys())
    return {"version": capabilities.get("version"), "exposed_count": len(names),
            "covered_count": len(names & results.keys()), "unmapped_tools": unmapped,
            "all_exposed_tools_routed": not unmapped,
            "full_catalog_available": all(item["available"] for item in results.values()),
            "tools": results}


def gate(job, capabilities, stage):
    spec = importlib.util.spec_from_file_location("makera_job_checks", SKILL / "scripts/check_job.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_job(job, stage, capabilities)


def task_spec(job):
    """TaskStore accepts a different schema. Preserve the full workflow as a sidecar."""
    checked = gate(job, None, "plan")
    missing = ["job." + item["path"] for item in checked["missing"] + checked["blocking"]]
    if not checked["ready_for_requested_stage"] and not missing:
        missing.append("job.readonly_or_unready_scope")
    model = job.get("model", {})
    if model.get("path") and model.get("sha256"):
        file = Path(model["path"])
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != model["sha256"]:
            missing.append("job.model_changed")
    automatic = [op for op in job.get("operations", []) if op.get("execution_mode") != "manual"]
    manual = [op for op in job.get("operations", []) if op.get("execution_mode") == "manual"]
    missing.extend("external_step:" + op["id"] for op in manual)
    material = job.get("material", {})
    specification = {
        "model_path": job.get("model", {}).get("path"),
        "stock_mm": job.get("stock", {}).get("nominal_mm", {}),
        "material": material.get("family"),
        "work_origin": job.get("datum", {}).get("description"),
        "tools": [{**tool, "type": tool.get("kind")} for tool in job.get("tools", [])],
        "operations": [{**op, "type": op.get("kind"), "region": op.get("geometry_region")}
                       for op in automatic],
        "output_directory": job.get("output_directory"),
        "deliver_nc": job.get("deliverables", {}).get("nc", False),
        "units": job.get("units"), "unresolved_inputs": sorted(set(missing)),
        "workflow_job": job, "source_job_revision": job.get("revision"),
    }
    if "through_cut_allowance_mm" in job:
        specification["through_cut_allowance_mm"] = job["through_cut_allowance_mm"]
    return specification


def operation_calls(job, operation_id, binding, capabilities, tool_list):
    capabilities = unwrap(capabilities)
    checked = gate(job, capabilities, "cam")
    if not checked["ready_for_requested_stage"]:
        return {"ready": False, "calls": [], "job_checks": checked}
    catalog = audit(capabilities, tool_list)
    op = next((op for op in job["operations"] if op["id"] == operation_id), None)
    if not op or op.get("kind") not in {"contour", "drill"} or op.get("execution_mode") == "manual":
        raise ValueError("Only automatic native contour/drill calls are implemented")
    if binding.get("job_revision") != job["revision"]:
        raise ValueError("Binding belongs to a different job revision")
    project = Path(binding["project_path"]).resolve(strict=True)
    snapshot = binding["project_snapshot"]
    digest = hashlib.sha256(project.read_bytes()).hexdigest()
    if project.suffix.lower() != ".mks" or snapshot.get("sha256") != digest:
        raise ValueError("Re-read the saved MKS: project snapshot is stale or invalid")
    if Path(snapshot.get("path", "")).resolve() != project:
        raise ValueError("Project snapshot points to another file")
    geometry = binding["geometry_uuids"]
    known = {item["uuid"] for item in snapshot.get("svg_geometry", [])}
    if not geometry or len(set(geometry)) != len(geometry) or any(uid not in known for uid in geometry):
        raise ValueError("Choose distinct geometry UUIDs from the current saved project")
    depth = op["depth_mm"]
    height = snapshot.get("stock", {}).get("height")
    if not isinstance(height, (int, float)) or isinstance(height, bool) or not math.isfinite(height) or depth > min(10, height):
        raise ValueError("Depth exceeds the native development adapter's stock/10 mm limit")
    calls = []

    def call(name, arguments):
        if not catalog["tools"][name]["available"]:
            raise ValueError("Tool/capability unavailable in the current connection: " + name)
        calls.append({"tool": name, "arguments": arguments})

    index = binding.get("operation_index")
    if index is None:
        call("create_2d_contour" if op["kind"] == "contour" else "create_2d_drilling",
             {"geometry_uuids": geometry, "end_depth_mm": depth})
        call("get_studio_operations", {})
        return {"ready": True, "phase": "create_only", "calls": calls,
                "next": "Record the returned operation_index and refresh project_snapshot before configuring"}
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("Use the actual nonnegative operation_index returned by Studio")
    saved = next((item for item in snapshot.get("operations", []) if item.get("index") == index), {})
    if binding.get("native_operation_id") is None or binding["native_operation_id"] != saved.get("id"):
        raise ValueError("Bind the actual saved native operation id, not only its current list index")
    if (saved.get("type") != {"contour": 1, "drill": 2}[op["kind"]]
            or saved.get("geometry_uuids") != geometry
            or abs(saved.get("depth", {}).get("endDepth", -1) - depth) > 0.000001):
        raise ValueError("Saved native operation does not match the planned type/geometry/depth")
    for name in ("generate_studio_toolpath", "save_studio_project", "get_studio_operations"):
        if not catalog["tools"][name]["available"]:
            raise ValueError("Required downstream tool/capability unavailable: " + name)
    tool = next(tool for tool in job["tools"] if tool["id"] == op["tool_id"])
    native = binding["tool"]
    for field in ("group", "name", "material_profile"):
        if not isinstance(native.get(field), str) or not native[field].strip():
            raise ValueError("Resolve the existing native tool group/name/material profile: " + field)
    if native.get("mode") == "register":
        if tool["kind"] not in {"endmill", "drill"} or (tool["kind"] == "endmill" and tool.get("geometry") != "flat"):
            raise ValueError("Only flat endmills and drills have verified native registration")
        if any(tool[key] > 100 for key in ("diameter_mm", "shank_mm", "flute_length_mm")):
            raise ValueError("Tool dimensions exceed the native registration range")
        fields = {"spindle_speed": "rpm", "feedrate": "feed_mm_min", "plunge_feedrate": "plunge_mm_min",
                  "step_down": "stepdown_mm", "step_over_mm": "step_over_mm"}
        parameters = {key: op.get(value) for key, value in fields.items()}
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0 for value in parameters.values()):
            raise ValueError("All five native parameters must be explicit; do not invent step_over_mm")
        if parameters["step_down"] >= tool["flute_length_mm"] or parameters["step_over_mm"] > tool["diameter_mm"]:
            raise ValueError("Cut increments exceed the native tool geometry limits")
        call("register_studio_tool", {"operation_index": index, "group": native["group"],
             "specification": {"kind": tool["kind"], "name": native["name"], "diameter": tool["diameter_mm"],
                               "shank_diameter": tool["shank_mm"], "flute_length": tool["flute_length_mm"]},
             "material_profile": native["material_profile"], "parameters": parameters})
    elif native.get("mode") != "select_existing":
        raise ValueError("Explicitly choose register or select_existing; do not overwrite by default")
    call("select_studio_tool", {"operation_index": index, "group": native["group"],
         "name": native["name"], "material_profile": native["material_profile"]})
    # Selection is a checkpoint: compare expected_saved_tool_after_generation with the job first.
    return {"ready": True, "phase": "configure_only", "calls": calls,
            "after_verification": [{"tool": "generate_studio_toolpath", "arguments": {"operation_index": index}},
                                   {"tool": "save_studio_project", "arguments": {}},
                                   {"tool": "get_studio_operations", "arguments": {}}],
            "next": "Compare selected expected parameters with this job before generating; inspect every result"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("--capabilities", required=True, type=Path)
    audit_parser.add_argument("--tools", required=True, type=Path)
    task_parser = sub.add_parser("task-spec")
    task_parser.add_argument("job", type=Path)
    op_parser = sub.add_parser("operation-calls")
    op_parser.add_argument("job", type=Path)
    op_parser.add_argument("operation_id")
    for field in ("binding", "capabilities", "tools"):
        op_parser.add_argument("--" + field, required=True, type=Path)
    args = parser.parse_args()
    read = lambda path: json.loads(path.read_text(encoding="utf-8-sig"))
    try:
        if args.command == "audit":
            result = audit(read(args.capabilities), read(args.tools))
            status = 0 if result["full_catalog_available"] and result["all_exposed_tools_routed"] else 2
        elif args.command == "task-spec":
            result, status = {"specification": task_spec(read(args.job))}, 0
        else:
            result = operation_calls(read(args.job), args.operation_id, read(args.binding),
                                     read(args.capabilities), read(args.tools))
            status = 0 if result["ready"] else 2
    except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration) as exc:
        result, status = {"ready": False, "calls": [], "error": str(exc)}, 3
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
