"""Standalone MCP-to-Qt bridge. No Computer Use or accessibility dependency."""
from __future__ import annotations

import ctypes
import hashlib
import json
import math
import re
import threading
import time
from ctypes import wintypes
from pathlib import Path

import frida

from .config import Settings
from .project import inspect_project


class NativeStudio:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.lock = threading.RLock()
        self.session = None
        self.script = None
        self.pid = None
        self.hwnd = None
        self.managed_project = None
        self.pending_tools = {}

    def connect(self) -> dict:
        with self.lock:
            if self.script:
                return {"connected": True, "pid": self.pid, "backend": "native_qt"}
            device = frida.get_local_device()
            processes = [item for item in device.enumerate_processes() if item.name.lower() == "makerastudio.exe"]
            if len(processes) != 1:
                raise RuntimeError(f"Expected one MakeraStudio process, found {len(processes)}")
            process = processes[0]
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            windows = []
            @callback_type
            def visit(hwnd, _):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == process.pid:
                    title = ctypes.create_unicode_buffer(1024)
                    user32.GetWindowTextW(hwnd, title, 1024)
                    if title.value.startswith("MakeraStudio"):
                        windows.append(int(hwnd))
                return True
            user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
            user32.EnumWindows(visit, 0)
            if not windows:
                raise RuntimeError("No Studio top-level window found")
            session = device.attach(process.pid)
            try:
                script = session.create_script(Path(__file__).with_name("native_agent.js").read_text(encoding="utf-8"))
                script.load()
                identity = script.exports_sync.identity()
                compatible = json.loads((self.settings.root / "native-compatibility.json").read_text(encoding="utf-8"))
                if identity["architecture"] != "x64" or hashlib.sha256(Path(identity["executable"]).read_bytes()).hexdigest() != compatible["studio_sha256"]:
                    raise RuntimeError("Studio executable changed; native adapter must be revalidated")
                windows = [hwnd for hwnd in windows if script.exports_sync.window_identity(hex(hwnd)) == {"name": "MainWindow", "type": "MainWindow"}]
                if len(windows) != 1:
                    raise RuntimeError("Expected one native Studio MainWindow; startup may still be pending")
                state = script.exports_sync.state(hex(windows[0]))
            except Exception:
                session.detach()
                raise
            self.session, self.script = session, script
            self.pid, self.hwnd = process.pid, windows[0]
            return {"connected": True, "pid": self.pid, "backend": "native_qt", "window_title": state["window_title"]}

    def close(self):
        with self.lock:
            if self.session:
                try:
                    self.session.detach()
                finally:
                    self.session = self.script = None
                    self.managed_project = None
                    self.pending_tools = {}

    def _call(self, method: str, *args):
        with self.lock:
            self.connect()
            return getattr(self.script.exports_sync, method)(hex(self.hwnd), *args)

    def read(self, include_tree=False) -> dict:
        try:
            state = self._call("state")
            return {"available": True, **state, "backend": "native_qt", "requires_computer_use": False}
        except Exception as exc:
            return {"available": False, "backend": "native_qt", "error_type": type(exc).__name__,
                    "reason": str(exc)[:240], "requires_computer_use": False}

    def describe(self) -> list:
        return self._call("describe_cam")

    def stock_fields(self) -> list:
        return self._call("stock_fields")

    def _require_managed(self):
        if self.managed_project is None:
            raise RuntimeError('Open an isolated project with this MCP before editing CAM')
        state = self.read()
        if not state.get('available') or self.managed_project.stem not in state['window_title']:
            raise RuntimeError('Studio changed projects; refusing CAM mutation')

    def operations(self) -> dict:
        with self.lock:
            self._require_managed()
            return {'live': self._call('path_summary'), 'saved': inspect_project(str(self.managed_project)),
                    'note': 'Live values may be unsaved; calculation evidence comes from the saved native scene'}

    def _wait_modal(self, expected, timeout=8):
        deadline = time.monotonic() + timeout
        while True:
            live = self._call('path_summary')
            if live['modal_type'] == expected:
                return live
            if time.monotonic() > deadline:
                raise RuntimeError(f"Expected dialog {expected!r}, found {live['modal_type']!r}")
            time.sleep(.1)

    def register_tool(self, operation_index: int, group: str, specification: dict, material: str, parameters: dict) -> dict:
        """Native library upsert, with geometry and parameter table readback; no machine commands."""
        with self.lock:
            self._require_managed()
            if not isinstance(specification, dict) or set(specification) != {'kind', 'name', 'diameter', 'shank_diameter', 'flute_length'}:
                raise ValueError('Expected kind/name/diameter/shank_diameter/flute_length')
            if specification['kind'] not in ('endmill', 'drill') or not isinstance(specification['name'], str) or not specification['name']:
                raise ValueError('Invalid tool kind or name')
            for key in ('diameter', 'shank_diameter', 'flute_length'):
                if isinstance(specification[key], bool) or not isinstance(specification[key], (int, float)) or not math.isfinite(specification[key]) or not 0 < specification[key] <= 100:
                    raise ValueError('Invalid tool geometry')
            required = {'spindle_speed','feedrate','plunge_feedrate','step_down','step_over_mm'}
            if set(parameters) != required or any(isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) or v <= 0 for v in parameters.values()):
                raise ValueError('Expected explicit positive cutting parameters')
            if parameters['step_down'] >= specification['flute_length'] or parameters['step_over_mm'] > specification['diameter']:
                raise ValueError('Cut increment exceeds tool geometry')
            self._wait_modal(None)
            if not self._call('open_tool_editor', operation_index)['queued']:
                raise RuntimeError('Cannot open native tool library')
            self._wait_modal('CutterLibWgt')
            self._call('select_tool_group', group)
            tables = self._call('tool_tables')
            tool_table = next(t for t in tables if t['name'] == 'tableWidget_tool')
            existing = [row for row in tool_table['values'] if row[0] == specification['name']]
            if len(existing) > 1:
                raise RuntimeError('Duplicate tool name in group')
            if existing:
                self._call('select_library_tool', specification['name'], material)
                self._call('tool_library_action', 'edit_tool')
            else:
                self._call('tool_library_action', 'add_tool')
            self._wait_modal('CutterEditDlg')
            configured = self._call('configure_tool_dialog', specification)
            if not all(configured['invoked']):
                raise RuntimeError('Native tool geometry update rejected')
            self._call('tool_edit_parameters_page')
            self._call('barrier')
            updated = self._call('configure_tool_test_material', material, parameters)
            if not all(updated['invoked']):
                raise RuntimeError('Native material parameter update rejected')
            self._call('apply_tool_material')
            self._call('barrier')
            self._call('save_tool_dialog')
            self._wait_modal('CutterLibWgt')
            selected = self._call('select_library_tool', specification['name'], material)
            if not selected['material_selected']:
                raise RuntimeError('Saved material profile was not selectable')
            tables = self._call('tool_tables')
            info_table = next(t for t in tables if t['name'] == 'tableWidget_tool_infomation')
            info = info_table['values']
            labels = info_table['row_labels']
            def geometry_value(label):
                matches = [row[0] for row, header in zip(info, labels) if header and re.split(r'[（(]', header)[0].strip() == label]
                if len(matches) != 1:
                    raise RuntimeError(f'Unrecognized tool information row {label!r}: {labels}')
                return float(matches[0])
            rates = next(t for t in tables if t['name'] == 'tableWidget_material')['values']
            matches = [row for row in rates if row[0] == material]
            verified = (len(info) >= 7 and info[0][0] == specification['name']
                and abs(geometry_value('直径')-specification['diameter']) < 1e-6
                and abs(geometry_value('刃长')-specification['flute_length']) < 1e-6
                and abs(geometry_value('刀柄直径')-specification['shank_diameter']) < 1e-6
                and len(matches)==1 and all(abs(float(matches[0][col])-parameters[key]) < 1e-6
                    for key,col in {'spindle_speed':1,'feedrate':2,'plunge_feedrate':3,'step_down':4,'step_over_mm':5}.items()))
            self._call('tool_library_action', 'close')
            self._wait_modal(None)
            return {'verified':verified,'specification':specification,'material_profile_label':material,
                    'native_geometry_table':info,'native_geometry_row_labels':labels,'native_parameter_rows':matches,'machine_commands_sent':False,
                    'machining_ready':False,'note':'Library settings only. Shoulder/stickout, tip geometry and actual cutting remain unverified.'}

    def select_tool(self, operation_index: int, group: str, name: str, material: str) -> dict:
        with self.lock:
            self._require_managed()
            self._wait_modal(None)
            self._call('open_tool_editor', operation_index)
            self._wait_modal('CutterLibWgt')
            self._call('select_tool_group', group)
            selected = self._call('select_library_tool', name, material)
            if not selected['material_selected']:
                raise RuntimeError('Requested tool material parameters are missing')
            tables = self._call('tool_tables')
            info = next(t for t in tables if t['name']=='tableWidget_tool_infomation')
            values = {re.split(r'[（(]',header)[0].strip():row[0] for header,row in zip(info['row_labels'],info['values']) if header}
            saved_operation = inspect_project(str(self.managed_project))['operations'][operation_index]
            if values.get('类型') != {0:'平底刀',1:'平底刀',2:'钻头'}.get(saved_operation['type']):
                raise ValueError('Selected tool type does not match the operation')
            rate_rows = next(t for t in tables if t['name']=='tableWidget_material')['values']
            rate = next(row for row in rate_rows if row[0]==material)
            expected = {'toolName':name,'diameter':float(values['直径']),'fluteLength':float(values['刃长']),
                        'handleDiameter':float(values['刀柄直径']), 'spindleSpeed':float(rate[1]),
                        'feedrate':float(rate[2]),'plungeFeedrate':float(rate[3]),'stepDown':float(rate[4]),'stepOverMM':float(rate[5])}
            self._call('tool_library_action', 'choose')
            self._wait_modal(None)
            live = self._call('path_summary')
            editor = live['paths'][operation_index]
            self.pending_tools[operation_index] = expected
            return {'verified':editor['tool']==name,'verification_scope':'native editor selection only',
                    'editor':editor,'expected_saved_tool_after_generation':expected,'parameter_commit_stage':'generate_studio_toolpath',
                    'recalculation_required':True,'machine_commands_sent':False}

    def create_contour(self, geometry_uuids: list[str], end_depth_mm: float) -> dict:
        return self._create_2d(geometry_uuids, end_depth_mm, 'contour')

    def create_drilling(self, geometry_uuids: list[str], end_depth_mm: float) -> dict:
        return self._create_2d(geometry_uuids, end_depth_mm, 'drilling')

    def create_pocket(self, geometry_uuids: list[str], end_depth_mm: float) -> dict:
        return self._create_2d(geometry_uuids, end_depth_mm, 'pocket')

    def _create_2d(self, geometry_uuids: list[str], end_depth_mm: float, kind: str) -> dict:
        """Create a native planar operation, verify geometry and depth persistence."""
        with self.lock:
            self._require_managed()
            types = {'pocket': (0, '2D区域清理'), 'contour': (1, '2D轮廓'), 'drilling': (2, '2D钻孔')}
            if kind not in types:
                raise ValueError('Unsupported planar operation kind')
            if not isinstance(geometry_uuids, list) or not geometry_uuids or len(set(geometry_uuids)) != len(geometry_uuids):
                raise ValueError('Expected distinct SVG geometry UUIDs')
            if isinstance(end_depth_mm, bool) or not isinstance(end_depth_mm, (int, float)) or not math.isfinite(end_depth_mm) or end_depth_mm <= 0:
                raise ValueError('End depth must be a finite positive number in mm')
            saved = self.save_project()
            if not saved['saved']:
                raise RuntimeError('Cannot establish saved project baseline')
            known = {item['uuid'] for item in saved['project']['svg_geometry']}
            if any(uid not in known for uid in geometry_uuids):
                raise ValueError('Geometry UUID must identify a verified SVG shape in the managed project')
            if end_depth_mm > min(10, saved['project']['stock']['height']):
                raise ValueError('Test contour depth exceeds stock or development limit')
            live = self._call('path_summary')
            if live['modal_type']:
                raise RuntimeError('Resolve current Studio dialog before creating an operation')
            baseline_count = len(live['paths'])
            if not self._call('open_path_chooser')['queued']:
                raise RuntimeError('Studio rejected opening the path chooser')
            deadline = time.monotonic() + 8
            while self._call('path_summary')['modal_type'] != 'AddKnifeTypeDlg':
                if time.monotonic() > deadline:
                    raise RuntimeError('Path chooser was queued but did not appear')
                time.sleep(.1)
            if not self._call('choose_path_type', types[kind][1])['queued']:
                raise RuntimeError('Studio rejected contour creation')
            while True:
                live = self._call('path_summary')
                if not live['modal_type'] and len(live['paths']) == baseline_count + 1:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError('Contour creation was not verified')
                time.sleep(.1)
            result = self._call('configure_contour', baseline_count, geometry_uuids, end_depth_mm, kind)
            if not all(result['invoked']):
                raise RuntimeError('Contour configuration invocation failed; inspect current project')
            interval = self._call('set_operation_depths', baseline_count, 0.0, end_depth_mm)
            if not all(interval['invoked']):
                raise RuntimeError('Initial depth interval was not committed; inspect created operation')
            saved = self.save_project()
            operation = saved['project']['operations'][baseline_count] if saved['saved'] else {}
            verified = (operation.get('type') == types[kind][0] and operation.get('geometry_uuids') == geometry_uuids
                        and abs(operation.get('depth', {}).get('startDepth', -1)) < 1e-6
                        and abs(operation.get('depth', {}).get('endDepth', -1) - end_depth_mm) < 1e-6)
            return {'created_and_saved': verified, 'operation_index': baseline_count, 'operation': operation,
                    'tool_selection': 'current Studio default tool; inspect returned tool parameters before use',
                    'calculated': False, 'machining_ready': False}

    def set_operation_depths(self, operation_index: int, start_depth_mm: float, end_depth_mm: float) -> dict:
        """Commit an explicit planar depth interval; require fresh calculation afterward."""
        with self.lock:
            self._require_managed()
            if isinstance(operation_index, bool) or not isinstance(operation_index, int) or operation_index < 0:
                raise ValueError('Expected a nonnegative operation index')
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                   for v in (start_depth_mm, end_depth_mm)) or not 0 <= start_depth_mm < end_depth_mm <= 10:
                raise ValueError('Expected 0 <= start depth < end depth <= 10 mm')
            baseline = inspect_project(str(self.managed_project))
            if operation_index >= len(baseline['operations']) or baseline['operations'][operation_index]['type'] not in (0, 1, 2):
                raise ValueError('Expected an existing planar pocket/contour/drilling operation')
            if end_depth_mm > baseline['stock']['height']:
                raise ValueError('Depth exceeds stock height')
            live = self._call('path_summary')
            if live['modal_type']:
                raise RuntimeError('Resolve the active Studio dialog before editing depth')
            if len(live['paths']) != len(baseline['operations']):
                raise RuntimeError('Live and saved operation order/count must be synchronized before depth editing')
            result = self._call('set_operation_depths', operation_index, start_depth_mm, end_depth_mm)
            if not all(result['invoked']):
                raise RuntimeError('Depth editing failed; inspect current operation before retrying')
            saved = self.save_project()
            operation = saved['project']['operations'][operation_index] if saved['saved'] else {}
            depth = operation.get('depth') or {}
            verified = (operation.get('id') == baseline['operations'][operation_index].get('id')
                        and abs(depth.get('startDepth', -1) - start_depth_mm) < 1e-6
                        and abs(depth.get('endDepth', -1) - end_depth_mm) < 1e-6)
            return {'verified': verified, 'operation_index': operation_index, 'operation': operation,
                    'recalculation_required': True, 'simulation_verified': False,
                    'machine_commands_sent': False, 'machining_ready': False}

    def generate_toolpath(self, operation_index: int) -> dict:
        with self.lock:
            self._require_managed()
            if isinstance(operation_index, bool) or not isinstance(operation_index, int) or operation_index < 0:
                raise ValueError('Expected a nonnegative operation index')
            baseline_project = inspect_project(str(self.managed_project))
            if operation_index >= len(baseline_project['operations']):
                raise ValueError('Saved operation index does not exist')
            operation = baseline_project['operations'][operation_index]
            if operation['type'] not in (0,1,2) or not operation['geometry_uuids']:
                raise ValueError('Only configured planar pocket/contour/drilling operations with selected geometry are supported')
            live = self._call('path_summary')
            if live['modal_type']:
                raise RuntimeError('Resolve current Studio dialog before calculation')
            if operation_index >= len(live['paths']):
                raise RuntimeError('Live operation order does not match saved operations')
            old_ids = [mesh['uuid'] for mesh in operation['toolpath_meshes']]
            if not self._call('generate_path', operation_index)['queued']:
                raise RuntimeError('Native contour calculation invocation rejected')
            deadline = time.monotonic() + 30
            last = operation
            while time.monotonic() < deadline:
                live = self._call('path_summary')
                if live['modal_type']:
                    # Calculation displays its own short-lived modal progress window.
                    # Never save behind a modal or mistake it for a completed result.
                    time.sleep(.25)
                    continue
                saved = self.save_project()
                if saved['saved']:
                    last = saved['project']['operations'][operation_index]
                    ids = [mesh['uuid'] for mesh in last['toolpath_meshes']]
                    pending = self.pending_tools.get(operation_index)
                    tool = last['tools'][0] if len(last['tools'])==1 else {}
                    tool_matches = not pending or all((tool.get(key)==value if isinstance(value,str)
                        else isinstance(tool.get(key),(int,float)) and abs(tool[key]-value)<1e-6) for key,value in pending.items())
                    if last['calculation_evidence_verified'] and (not old_ids or ids != old_ids) and tool_matches:
                        self.pending_tools.pop(operation_index,None)
                        return {'verified': True, 'operation': last, 'project_path': str(self.managed_project),
                                'verification': 'native state 1 + newly generated linked nonempty finite gcode_polyline vertex buffers in saved MKS',
                                'simulation_verified': False, 'nc_export_verified': False, 'machining_ready': False,
                                'machine_commands_sent': False}
                time.sleep(.25)
            return {'verified': False, 'outcome': 'new_calculation_not_verified_before_timeout',
                    'modal_type': live.get('modal_type'), 'last_saved_operation': last}

    def open_project(self, path: str) -> dict:
        project = Path(path).resolve(strict=True)
        allowed = (self.settings.root / "cases").resolve()
        if not project.is_relative_to(allowed):
            raise ValueError("This development adapter only opens isolated project copies under cases/")
        inspection = inspect_project(str(project))
        queued = self._call("open_project", str(project))
        if not queued["queued"]:
            raise RuntimeError("Native sltOpenProject invocation rejected")
        self._call("barrier")
        # A queued call is not success. Verify the resulting native project title.
        expected = project.stem
        deadline = time.monotonic() + 8
        state = None
        same_project_saved = False
        while time.monotonic() < deadline:
            state = self.read()
            modal = self._call('path_summary')['modal_type']
            if modal:
                if modal == 'MyMessageBox' and state.get('window_title') == f'MakeraStudio-[{expected}]' and not same_project_saved:
                    try:
                        same_project_saved = bool(self._call('save_before_reopening_same_test', expected)['queued'])
                    except Exception:
                        pass
                    if same_project_saved:
                        time.sleep(.2)
                        continue
                return {'opened': False, 'outcome': 'native_open_waiting_for_dialog', 'modal_type': modal, 'state': state}
            if state.get("available") and expected in state["window_title"]:
                self.managed_project = project
                self.pending_tools = {}
                return {"opened": True, "native_method": queued["method"], "path": str(project),
                        "state": state, "source": "native Qt slot + native property readback",
                        "existing_same_test_saved_before_reopen": same_project_saved}
            time.sleep(0.1)
        return {"opened": False, "outcome": "queued_but_not_verified", "state": state}

    def set_stock(self, dimensions: dict) -> dict:
        if self.managed_project is None:
            raise RuntimeError("Open an isolated project with this MCP before modifying stock")
        state = self.read()
        if not state.get("available") or self.managed_project.stem not in state["window_title"]:
            raise RuntimeError("Studio changed projects; refusing to modify stock")
        if not dimensions or set(dimensions) - {"x", "y", "z"}:
            raise ValueError("Expected one or more stock dimensions x/y/z")
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0
               for value in dimensions.values()):
            raise ValueError("Stock dimensions must be finite positive numbers in mm")
        queued = self._call("set_stock", dimensions)
        if not all(item["queued"] and item["commit_queued"] for item in queued):
            raise RuntimeError("One or more native stock changes were rejected; outcome may be partial")
        expected = {"x": "dspbLength", "y": "dspbWidth", "z": "dspbHeight"}
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            state = self.read()
            fields = self.stock_fields()
            values = {}
            for axis, ancestor in expected.items():
                matches = [item for item in fields if "/" + ancestor + "/" in item["path"] and "/widgetCusProjectInfo/wgtCusProjectInfo/" in item["path"]]
                if len(matches) == 1:
                    values[axis] = matches[0]["value"]
            if all(axis in values and abs(values[axis] - value) < 1e-6 for axis, value in dimensions.items()):
                return {"verified": True, "requested": dimensions, "readback": values,
                        "native_methods": ["QDoubleSpinBox.setValue(double)", "UnitDoubleSpinBox.editingFinished()"]}
            time.sleep(0.1)
        return {"verified": False, "requested": dimensions, "readback": values,
                "reason": "Native UI values did not match requested values"}

    def save_project(self) -> dict:
        if self.managed_project is None:
            raise RuntimeError("No managed isolated project is open")
        state = self.read()
        if not state.get("available") or self.managed_project.stem not in state["window_title"]:
            raise RuntimeError("Studio changed projects; refusing to save")
        before = self.managed_project.stat().st_mtime_ns
        queued = self._call("save_project")
        if not queued["queued"]:
            raise RuntimeError("Native save invocation rejected")
        self._call("barrier")
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.managed_project.stat().st_mtime_ns != before:
                try:
                    result = inspect_project(str(self.managed_project))
                    return {"saved": True, "method": queued["method"], "project": result}
                except (OSError, ValueError, KeyError):
                    pass
            time.sleep(0.1)
        return {"saved": False, "outcome": "queued_but_file_write_not_verified"}
