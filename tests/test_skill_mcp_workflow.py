"""Synthetic workflow/real MCP contract tests; no live Studio or device access."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

import anyio
import jsonschema
from mcp.shared.memory import create_connected_server_and_client_session
from PIL import Image
from websockets.sync.server import serve

from makera_mcp.config import Settings
from makera_mcp.native import NativeStudio
from makera_mcp.project import inspect_project
from makera_mcp.server import build_server
from makera_mcp.tasks import validate_spec
import test_skill_job_gate
import test_model_placement


SKILL = Path(__file__).resolve().parents[1] / "skills/makera-studio-cam"
SPEC = importlib.util.spec_from_file_location("skill_mcp_workflow", SKILL / "scripts/mcp_workflow.py")
WORKFLOW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKFLOW)
CATALOG = json.loads((SKILL / "assets/mcp-tool-map.json").read_text(encoding="utf-8"))
NAMES = [item["name"] for item in CATALOG["tools"]]
CAPS = {"version": "0.8.3", "supported": sorted({cap for item in CATALOG["tools"] for cap in item["requires"]} | {"native_explicit_through_cut_allowance"})}
GEOMETRY = "12345678-1234-1234-1234-123456789abc"


def write_project(path, created=False, kind=1):
    data = struct.pack("<ffffff", 0, 0, 0, 10, 8, 0)
    binary = (struct.pack("<I", 8) + b"scene_v3" + struct.pack("<I", 8) + b"SvgShape"
              + struct.pack("<I", 36) + GEOMETRY.encode() + b"\0" * 98 + struct.pack("<I", len(data)) + data)
    operations = [{"id": "synthetic-native", "type": kind, "selectedmeshes": [GEOMETRY],
                   "depth": {"startDepth": 0, "endDepth": 0.5}}] if created else []
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("makera.bin", binary)
        archive.writestr("makera.prj", json.dumps({"project": {"strProjectName": "synthetic",
            "stStockInfo": {"length": 80, "width": 60, "height": 5.5},
            "lstCoordinate": {"0": {"path": operations}}}}))


class WorkflowPayloadTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_skill_job_gate.JobGateTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.generic_job()
        self.job = self.fixture.job
        self.job["output_directory"] = str(Path(self.fixture.temp.name) / "output")
        self.job["operations"][0]["step_over_mm"] = 0.2
        project = Path(self.fixture.temp.name) / "synthetic.mks"
        write_project(project)
        self.binding = {"job_revision": 1, "project_path": str(project),
            "project_snapshot": inspect_project(str(project)), "geometry_uuids": [GEOMETRY],
            "tool": {"group": "SyntheticGroup", "name": "SyntheticMill", "material_profile": "SyntheticMaterial", "mode": "register"}}

    def calls(self, tools=None, caps=None):
        return WORKFLOW.operation_calls(self.job, "feature", self.binding,
                                        CAPS if caps is None else caps, NAMES if tools is None else tools)

    def relief_binding(self, configured=False, layered=True):
        project=Path(self.binding['project_path'])
        test_model_placement.fixture(project,center=(20,15,-3))
        with zipfile.ZipFile(project) as z:
            scene=z.read('makera.bin');data=json.loads(z.read('makera.prj'))
        data['project']['stStockInfo']['height']=6
        if configured:
            data['project']['lstCoordinate']={'0':{'path':[{'id':'synthetic-relief','type':6,
                'selectedmeshes':[test_model_placement.UUID],'finishing':[not layered],'machining_allowance':[.2]}]}}
        with zipfile.ZipFile(project,'w') as z:
            z.writestr('makera.bin',scene);z.writestr('makera.prj',json.dumps(data))
        self.binding.update(project_snapshot=inspect_project(str(project)),geometry_uuids=[test_model_placement.UUID])
        if configured:self.binding.update(operation_index=0,native_operation_id='synthetic-relief')
        self.job['operations'][0].update(kind='relief_3d',depth_mm=6,relief={'layered':True,'allowance_mm':.2})

    def test_relief_creation_binds_model_not_svg_or_planar_depth(self):
        self.relief_binding()
        result=self.calls()
        self.assertTrue(result['ready'],result)
        first=result['calls'][0]
        self.assertEqual(first['tool'],'create_3d_relief')
        self.assertEqual(first['arguments']['model_uuid'],test_model_placement.UUID)
        self.assertEqual(first['arguments']['project_sha256'],self.binding['project_snapshot']['sha256'])
        self.assertNotIn('end_depth_mm',first['arguments'])

    def test_relief_settings_changes_are_a_separate_checkpoint(self):
        self.relief_binding(configured=True,layered=False)
        result=self.calls()
        self.assertEqual(result['phase'],'relief_settings_only')
        self.assertEqual(result['calls'][0]['tool'],'configure_studio_3d_relief')
        self.assertFalse(any(c['tool']=='set_studio_operation_depths' for c in result['calls']))

    def test_relief_tool_selection_and_generation_remain_separate(self):
        self.relief_binding(configured=True)
        result=self.calls()
        self.assertEqual(result['phase'],'configure_only')
        self.assertEqual(result['calls'][-1]['tool'],'select_studio_tool')
        self.assertEqual(result['after_verification'][0]['tool'],'generate_studio_toolpath')
        del self.job['operations'][0]['relief']
        with self.assertRaises(ValueError):self.calls()

    def configured(self, kind=1):
        write_project(Path(self.binding["project_path"]), created=True, kind=kind)
        self.binding.update(operation_index=0, native_operation_id="synthetic-native",
                            project_snapshot=inspect_project(self.binding["project_path"]))

    def test_task_schema_mapping_is_accepted_without_losing_workflow(self):
        spec = WORKFLOW.task_spec(self.job)
        normalized, missing = validate_spec(spec)
        self.assertEqual(missing, [])
        self.assertEqual(normalized["tools"][0]["type"], "endmill")
        self.assertEqual(normalized["operations"][0]["region"], "synthetic geometry")
        self.assertEqual(normalized["workflow_job"]["requirements"], self.job["requirements"])
        self.assertFalse(normalized["deliver_nc"])

    def test_manual_step_is_preserved_and_not_misreported_as_complete(self):
        self.job["operations"].append({"id": "external", "execution_mode": "manual", "kind": "external"})
        spec = WORKFLOW.task_spec(self.job)
        self.assertIn("external_step:external", spec["unresolved_inputs"])
        self.assertEqual(len(spec["workflow_job"]["operations"]), 2)

    def test_changed_model_and_discussion_do_not_become_ready_tasks(self):
        Path(self.job["model"]["path"]).write_text("changed", encoding="utf-8")
        self.assertIn("job.model_changed", WORKFLOW.task_spec(self.job)["unresolved_inputs"])
        self.job["scope"] = "discussion"
        self.assertIn("job.readonly_or_unready_scope", WORKFLOW.task_spec(self.job)["unresolved_inputs"])

    def test_create_phase_never_guesses_index_or_generates(self):
        result = self.calls()
        self.assertEqual(result["phase"], "create_only")
        self.assertEqual([call["tool"] for call in result["calls"]], ["create_2d_contour", "get_studio_operations"])
        self.assertNotIn("after_verification", result)

    def test_registration_maps_all_native_fields_and_defers_generation(self):
        self.configured()
        result = self.calls()
        first = result["calls"][0]
        self.assertEqual(first["tool"], "register_studio_tool")
        self.assertEqual(set(first["arguments"]["parameters"]), {"spindle_speed", "feedrate", "plunge_feedrate", "step_down", "step_over_mm"})
        self.assertEqual(first["arguments"]["specification"]["flute_length"], 6.5)
        self.assertNotIn("generate_studio_toolpath", [call["tool"] for call in result["calls"]])
        self.assertEqual(result["after_verification"][0]["tool"], "generate_studio_toolpath")

    def test_drilling_is_routed_to_actual_drilling_tool(self):
        self.job["operations"][0].update(kind="drill", hole_type="blind")
        self.job["tools"][0]["kind"] = "drill"
        self.assertEqual(self.calls()["calls"][0]["tool"], "create_2d_drilling")

    def chamfer_job(self):
        self.job['operations'][0].update(kind='chamfer',chamfer={'depth_mm':.5,'tip_offset_mm':.2,'side':'outside','direction':'climb'})
        self.job['tools'][0].update(kind='engraver',geometry='v',diameter_mm=3.175,included_angle_deg=90,tip_diameter_mm=.1)
        self.binding['tool']['mode'] = 'select_existing'

    def test_chamfer_requires_capability_not_just_schemas(self):
        self.chamfer_job()
        caps = {**CAPS,'supported':[c for c in CAPS['supported'] if c!='native_2d_chamfer_calculation_and_verification']}
        result = self.calls(caps=caps)
        self.assertFalse(result['ready'])
        self.assertEqual(result['calls'],[])

    def test_chamfer_creation_and_configuration_follow_selection_checkpoint(self):
        self.chamfer_job()
        self.assertEqual(self.calls()['calls'][0]['tool'],'create_2d_chamfer')
        self.configured(kind=13)
        result = self.calls()
        self.assertEqual([c['tool'] for c in result['calls']],['select_studio_tool'])
        self.assertEqual(result['after_verification'][0]['tool'],'configure_studio_2d_chamfer')
        self.assertEqual(result['after_verification'][1]['tool'],'generate_studio_toolpath')
        self.assertEqual(result['after_verification'][0]['arguments']['project_sha256'],self.binding['project_snapshot']['sha256'])

    def test_chamfer_missing_angle_or_unusable_cone_blocks_helper(self):
        self.chamfer_job()
        self.job['tools'][0]['included_angle_deg'] = None
        self.assertFalse(self.calls()['ready'])
        self.job['tools'][0]['included_angle_deg'] = 90
        self.job['operations'][0]['chamfer']['tip_offset_mm'] = 2
        self.assertFalse(self.calls()['ready'])

    def test_chamfer_size_is_separate_from_absolute_budget_and_generic_depth_setter(self):
        self.chamfer_job()
        self.job['operations'][0].update(start_depth_mm=.2,depth_mm=.5)
        self.job['operations'][0]['chamfer']['depth_mm'] = .3
        self.configured(kind=13)
        result = self.calls()
        args = result['after_verification'][0]['arguments']
        self.assertEqual(args['chamfer_depth_mm'],.3)
        self.assertEqual(args['start_depth_mm'],.2)
        self.assertNotIn('end_depth_mm',args)
        self.job['operations'][0]['chamfer']['depth_mm'] = .5
        self.assertFalse(self.calls()['ready'])

    def test_pocket_creation_and_explicit_depth_changes_are_separate_phases(self):
        self.job["operations"][0]["kind"] = "pocket"
        self.assertEqual(self.calls()["calls"][0]["tool"], "create_2d_pocket")
        self.configured(kind=0)
        self.job["operations"][0]["start_depth_mm"] = 0.1
        packets = self.calls()
        self.assertEqual(packets["phase"], "depth_only")
        self.assertEqual(packets["calls"][0]["arguments"]["start_depth_mm"], 0.1)
        self.assertNotIn("after_verification", packets)

    def test_existing_tool_selection_does_not_update_library(self):
        self.configured()
        self.binding["tool"]["mode"] = "select_existing"
        self.assertEqual([call["tool"] for call in self.calls()["calls"]], ["select_studio_tool"])

    def test_tabs_are_staged_only_after_tool_verification(self):
        self.configured()
        self.job['operations'][0]['tabs']={'count':2,'width_mm':0.3,'height_mm':0.2,'shape':'rectangle'}
        result=self.calls()
        self.assertEqual(result['after_verification'][0]['tool'],'configure_studio_contour_tabs')
        self.assertEqual(result['after_verification'][1]['tool'],'generate_studio_toolpath')
        self.assertTrue(result['tabs_require_post_commit_tool_reselection'])

    def test_explicit_allowance_is_forwarded_to_existing_depth_edit(self):
        self.configured()
        self.job['tools'][0]['flute_length_mm']=10
        self.job['operations'][0].update(depth_mm=5.8,through_cut_allowance_mm=0.3)
        result=self.calls()
        self.assertEqual(result['phase'],'depth_only')
        self.assertEqual(result['calls'][0]['arguments']['through_cut_allowance_mm'],0.3)

    def test_missing_or_nonfinite_stepover_is_not_invented(self):
        self.configured()
        for value in (None, float("nan"), 0):
            self.job["operations"][0]["step_over_mm"] = value
            with self.assertRaises(ValueError):
                self.calls()

    def test_stale_revision_snapshot_and_wrong_native_index_are_rejected(self):
        baseline = copy.deepcopy(self.binding)
        self.binding["job_revision"] = 0
        with self.assertRaises(ValueError):
            self.calls()
        self.binding = copy.deepcopy(baseline)
        Path(self.binding["project_path"]).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.calls()
        self.configured()
        self.binding["operation_index"] = 1
        with self.assertRaises(ValueError):
            self.calls()

    def test_old_connection_and_new_unmapped_tool_are_reported(self):
        old = copy.deepcopy(CAPS)
        old["version"] = "0.3.0"
        old["supported"].remove("native_2d_drilling_and_verified_peck_retraction_sequence")
        result = WORKFLOW.audit(old, NAMES + ["new_unmapped"])
        self.assertFalse(result["tools"]["create_2d_drilling"]["available"])
        self.assertEqual(result["unmapped_tools"], ["new_unmapped"])
        self.configured()
        with self.assertRaises(ValueError):
            self.calls(tools=[name for name in NAMES if name != "generate_studio_toolpath"])

    def test_plan_gate_failure_produces_no_mutation_packets(self):
        self.job["plan_confirmed"] = False
        result = self.calls()
        self.assertFalse(result["ready"])
        self.assertEqual(result["calls"], [])

    def test_catalog_and_payloads_match_real_registered_mcp_schemas(self):
        self.configured()
        packets = self.calls()
        with tempfile.TemporaryDirectory() as folder:
            server, camera = build_server(Settings(Path(folder)))
            try:
                async def verify():
                    async with create_connected_server_and_client_session(server) as client:
                        tools = (await client.list_tools()).tools
                        schemas = {tool.name: tool.inputSchema for tool in tools}
                        capabilities = json.loads((await client.call_tool("get_capabilities", {})).content[0].text)
                        audited = WORKFLOW.audit(capabilities, [tool.model_dump() for tool in tools])
                        self.assertTrue(audited["full_catalog_available"])
                        self.assertTrue(audited["all_exposed_tools_routed"])
                        self.assertEqual(audited["covered_count"], len(tools))
                        self.assertEqual(audited['covered_count'],len(CATALOG['tools']))
                        for call in packets["calls"] + packets["after_verification"]:
                            jsonschema.validate(call["arguments"], schemas[call["tool"]])
                anyio.run(verify)
            finally:
                camera.stop()

    def test_chamfer_schemas_and_capability_cover_staged_helper_packets(self):
        with tempfile.TemporaryDirectory() as folder:
            server,camera = build_server(Settings(Path(folder)))
            try:
                async def verify():
                    async with create_connected_server_and_client_session(server) as client:
                        schemas = {t.name:t.inputSchema for t in (await client.list_tools()).tools}
                        self.assertIn('create_2d_chamfer',schemas)
                        self.assertIn('configure_studio_2d_chamfer',schemas)
                        caps = json.loads((await client.call_tool('get_capabilities',{})).content[0].text)
                        self.assertEqual(caps['native_2d_chamfer_scope']['verified_tool_angles_deg'],[90])
                        self.assertIn('native_2d_chamfer_calculation_and_verification',caps['supported'])
                        self.chamfer_job(); self.configured(kind=13)
                        packets = self.calls()
                        for call in packets['calls']+packets['after_verification']:
                            jsonschema.validate(call['arguments'],schemas[call['tool']])
                anyio.run(verify)
            finally: camera.stop()


class MCPAuxiliaryIntegrationTests(unittest.TestCase):
    def test_real_mcp_tasks_video_evidence_and_nc_use_synthetic_resources(self):
        image = io.BytesIO()
        Image.new("RGB", (32, 24), "gray").save(image, "JPEG")
        stop = threading.Event()
        def handler(socket):
            self.assertEqual(socket.recv(timeout=2), "start_stream")
            try:
                while not stop.is_set():
                    socket.send(image.getvalue())
                    time.sleep(0.03)
            except Exception:
                pass
        with tempfile.TemporaryDirectory() as folder, serve(handler, "127.0.0.1", 0) as websocket:
            threading.Thread(target=websocket.serve_forever, daemon=True).start()
            root = Path(folder)
            project = root / "synthetic.mks"
            write_project(project)
            model = root / "model.svg"
            model.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
            nc = root / "sample.nc"
            nc.write_text("G21 G90\nG1 X2 F100\n", encoding="utf-8")
            url = f"ws://127.0.0.1:{websocket.socket.getsockname()[1]}/ws_video"
            server, camera = build_server(Settings(root, camera_url=url))
            try:
                # Only the Studio state read is a test double. No Frida attach or machine access.
                with patch.object(NativeStudio, "read", return_value={"available": False, "test_double": True}):
                    async def verify():
                        async with create_connected_server_and_client_session(server) as client:
                            async def call(name, arguments=None):
                                result = await client.call_tool(name, arguments or {})
                                self.assertFalse(result.isError, name)
                                text = json.loads(next(item.text for item in result.content if item.type == "text"))
                                return text, result
                            spec = {"model_path": str(model), "stock_mm": {"x": 80, "y": 60, "z": 5},
                                "material": "synthetic", "work_origin": "synthetic top", "output_directory": str(root / "output"),
                                "tools": [{"id": "t", "type": "endmill", "diameter_mm": 2, "shank_mm": 3}],
                                "operations": [{"type": "contour", "tool_id": "t", "depth_mm": 0.5,
                                    "region": "synthetic", "rpm": 1000, "feed_mm_min": 100, "stepdown_mm": 0.1}], "deliver_nc": False}
                            task, _ = await call("create_cam_task", {"specification": spec})
                            self.assertFalse(task["machine_execution_authorized"])
                            self.assertTrue((await call("list_cam_tasks"))[0]["tasks"])
                            self.assertEqual((await call("get_cam_task", {"task_id": task["id"]}))[0]["revision"], 1)
                            self.assertEqual((await call("get_next_cam_action", {"task_id": task["id"]}))[0]["stage"], "project_created")
                            revised, _ = await call("revise_cam_task", {"task_id": task["id"], "specification": spec})
                            self.assertEqual(revised["revision"], 2)
                            self.assertEqual(revised["stages"]["toolpaths_generated"]["status"], "invalidated")
                            self.assertTrue((await call("inspect_studio_project", {"path": str(project)}))[0]["geometry_only_detected"])
                            self.assertFalse((await call("inspect_nc_file", {"path": str(nc)}))[0]["machining_approved"])
                            await call("get_camera_status")
                            await call("set_task_context", {"task_id": task["id"], "operation": "synthetic revision 2 observation"})
                            await call("start_camera")
                            capture, result = await call("capture_frame")
                            self.assertEqual(sum(item.type == "image" for item in result.content), 1)
                            observation, result = await call("sample_frames", {"count": 2, "interval_s": 0.2})
                            self.assertEqual(sum(item.type == "image" for item in result.content), 2)
                            self.assertFalse(observation["observation"]["frames"][0]["source_timestamp_available"])
                            bundle, _ = await call("get_observation_bundle", {"count": 1, "interval_s": 0.2})
                            self.assertTrue(bundle["observation"]["context"]["studio_before"]["test_double"])
                            self.assertFalse((await call("list_visual_events", {"limit": 10}))[0]["anomaly_classifier_enabled"])
                            archive, _ = await call("export_event_evidence", {"observation_id": capture["observation"]["id"]})
                            self.assertTrue(Path(archive["archive_path"]).is_file())
                            self.assertTrue((await client.call_tool("sample_frames", {"count": 9})).isError)
                            await call("stop_camera")
                            self.assertEqual((await call("get_camera_status"))[0]["state"], "stopped")
                    anyio.run(verify)
            finally:
                camera.stop()
                stop.set()
                websocket.shutdown()


if __name__ == "__main__":
    unittest.main()
