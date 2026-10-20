"""Explicit software-only native pocket/depth smoke test using a user-supplied MKS.

Requires Studio to be idle with its current project saved and dialogs resolved.
Copies a geometry-only source, never overwrites it, and never transmits NC.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from makera_mcp.project import inspect_project

ROOT = Path(__file__).resolve().parents[1]


async def validate(args):
    source = args.source.resolve(strict=True)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    baseline = inspect_project(str(source))
    if baseline["native_path_entries"] or args.geometry_uuid not in {shape["uuid"] for shape in baseline["svg_geometry"]}:
        raise ValueError("Supply a geometry-only MKS and a verified SVG geometry UUID")
    if Path(args.filename).name != args.filename or Path(args.filename).suffix.lower() != ".mks":
        raise ValueError("Use a simple .mks destination filename")
    folder = ROOT / "cases/native_planar_validation"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / args.filename
    if target.exists():
        raise ValueError("Refusing to overwrite an existing project")
    shutil.copy2(source, target)
    report = {"transport": "real STDIO MCP", "machine_commands_sent": False,
              "physical_setup_reused": False, "calls": []}
    report_path = ROOT / "runtime/native-planar-mcp-validation.json"
    report_path.parent.mkdir(exist_ok=True)
    def persist():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    parameters = StdioServerParameters(command=sys.executable, args=["-m", "makera_mcp.server"], cwd=str(ROOT),
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "MAKERA_CAMERA_URL": "",
             "MAKERA_MCP_CONFIG": str(ROOT / "runtime/unconfigured-planar-test.json")})
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            async def call(name, arguments=None):
                response = await client.call_tool(name, arguments or {})
                if response.isError:
                    raise RuntimeError(str(response))
                value = json.loads(next(block.text for block in response.content if block.type == "text"))
                report["calls"].append({"tool": name, "result": value})
                persist()
                print(json.dumps({"tool": name, "verified": value.get("verified", value.get("opened", value.get("created_and_saved")))}, ensure_ascii=False), flush=True)
                return value
            capabilities = await call("get_capabilities")
            assert "native_pocket_calculation_and_verification" in capabilities["supported"]
            await call("connect_studio")
            assert (await call("open_studio_project", {"path": str(target)}))["opened"]
            created = await call("create_2d_pocket", {"geometry_uuids": [args.geometry_uuid], "end_depth_mm": 0.3})
            assert created["created_and_saved"] and created["operation"]["type"] == 0
            index = created["operation_index"]
            depths = await call("set_studio_operation_depths", {"operation_index": index, "start_depth_mm": 0.1, "end_depth_mm": 0.3})
            assert depths["verified"] and depths["recalculation_required"]
            first = await call("generate_studio_toolpath", {"operation_index": index})
            assert first["verified"] and not first["simulation_verified"] and not first["machining_ready"]
            first_ids = {mesh["uuid"] for mesh in first["operation"]["toolpath_meshes"]}
            depths = await call("set_studio_operation_depths", {"operation_index": index, "start_depth_mm": 0.2, "end_depth_mm": 0.6})
            assert depths["verified"]
            report["old_calculation_cleared_after_depth_change"] = not depths["operation"]["calculation_evidence_verified"]
            assert report["old_calculation_cleared_after_depth_change"], "Native depth edit left stale calculation marked valid"
            second = await call("generate_studio_toolpath", {"operation_index": index})
            assert second["verified"]
            assert first_ids != {mesh["uuid"] for mesh in second["operation"]["toolpath_meshes"]}
            z_min = min(mesh["bounds_in_serialized_coordinates_mm"]["z"][0] for mesh in second["operation"]["toolpath_meshes"])
            assert abs(z_min + 0.6) < 0.0001
            lowered = await call("set_studio_operation_depths", {"operation_index": index, "start_depth_mm": 0.0, "end_depth_mm": 0.1})
            assert lowered["verified"] and not lowered["operation"]["calculation_evidence_verified"]
            third = await call("generate_studio_toolpath", {"operation_index": index})
            assert third["verified"]
            final_z = min(mesh["bounds_in_serialized_coordinates_mm"]["z"][0] for mesh in third["operation"]["toolpath_meshes"])
            assert abs(final_z + 0.1) < 0.0001
            await call("get_studio_operations")
            assert (await call("save_studio_project"))["saved"]
            report.update(source_unchanged=hashlib.sha256(source.read_bytes()).hexdigest() == source_hash,
                          verified=True, final_z_min_mm=final_z, depth_decrease_verified=True,
                          vertices=sum(mesh["vertex_count"] for mesh in third["operation"]["toolpath_meshes"]))
            assert report["source_unchanged"]
            persist()
    print(json.dumps({key: report[key] for key in ("verified", "vertices", "source_unchanged", "machine_commands_sent")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--geometry-uuid", required=True)
    parser.add_argument("--filename", required=True)
    anyio.run(validate, parser.parse_args())
