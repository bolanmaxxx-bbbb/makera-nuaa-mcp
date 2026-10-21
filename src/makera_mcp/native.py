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
        self.pending_tabs = {}

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
                    self.pending_tabs = {}

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

    def stock_options(self) -> dict:
        with self.lock:
            self._require_managed()
            return self._call('stock_options')

    def configure_stock(self, specification: dict) -> dict:
        """Configure custom box/cylinder stock and exact native material labels."""
        with self.lock:
            self._require_managed()
            allowed = {'shape', 'dimensions_mm', 'material_category', 'material_name', 'stock_specification'}
            if not isinstance(specification, dict) or not specification or set(specification)-allowed:
                raise ValueError('Expected shape/dimensions_mm/material_category/material_name/stock_specification fields')
            shape = specification.get('shape')
            if shape is not None and shape not in ('box', 'cylinder'):
                raise ValueError('Stock shape must be box or cylinder')
            dimensions = specification.get('dimensions_mm', {})
            if not isinstance(dimensions, dict) or set(dimensions)-{'x', 'y', 'z', 'diameter'} or any(isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) or v<=0 for v in dimensions.values()):
                raise ValueError('Stock dimensions must be positive finite mm values')
            if shape and set(dimensions) != ({'x','y','z'} if shape=='box' else {'diameter','z'}):
                raise ValueError('Explicit shape changes require all dimensions for that shape')
            if dimensions and not shape:
                raise ValueError('Full stock dimensions require explicit box/cylinder shape')
            material = [specification.get(k) for k in ('material_category','material_name')]
            if any(v is not None for v in material) and not all(isinstance(v,str) and v.strip() for v in material):
                raise ValueError('Provide both exact native material category and name')
            preset = specification.get('stock_specification')
            if preset is not None and (not isinstance(preset,str) or not preset.strip() or not all(material) or dimensions or shape):
                raise ValueError('A stock catalog specification requires category/name and cannot be combined with custom shape/dimensions')
            if not dimensions and not shape and not all(material):
                raise ValueError('No stock configuration was requested')
            if self.pending_tabs:
                raise RuntimeError('Commit or reopen pending contour details before stock editing')
            baseline = self.save_project()
            if not baseline.get('saved'):
                raise RuntimeError('Cannot establish saved stock baseline')
            live = self._call('path_summary')
            if live['modal_type']:
                raise RuntimeError('Resolve active dialog before stock editing')
            if not self._call('stock_options')['editor_visible']:
                if not self._call('open_stock_editor')['queued']:
                    raise RuntimeError('Cannot open project stock settings')
                self._call('barrier')
            result = self._call('configure_stock_editor', {**specification,'dimensions_mm':dimensions})
            self._call('close_stock_editor')
            saved = self.save_project()
            stock = saved.get('project',{}).get('stock',{})
            keys = {'x':'length','y':'width','z':'height','diameter':'diameter'}
            dimensions_match = all(isinstance(stock.get(keys[k]),(int,float)) and abs(stock[keys[k]]-v)<1e-6 for k,v in dimensions.items())
            material_matches = not all(material) or ((stock.get('material') or {}).get('name')==material[0] and (stock.get('materialobj') or {}).get('materialSubcategoryName')==material[1])
            shape_matches = shape is None or stock.get('StockShape')=={'box':1,'cylinder':0}[shape]
            preset_record = stock.get('materialspec') or {}
            preset_matches = preset is None or preset_record.get('materialName')==preset
            if preset and preset_matches:
                preset_keys = ('diameter','height') if stock.get('StockShape')==0 else ('length','width','height')
                preset_matches = all(isinstance(stock.get(k),(int,float)) and isinstance(preset_record.get(k),(int,float)) and abs(stock[k]-preset_record[k])<1e-6 for k in preset_keys)
            verified = bool(result.get('verified') and saved.get('saved') and dimensions_match and material_matches and shape_matches and preset_matches)
            return {'verified':verified,'native_result':result,'saved_stock':stock,
                    'rollback_saved_verified':bool(result.get('rolled_back') and saved.get('saved') and stock==baseline['project']['stock']),
                    'project':saved.get('project'),'recalculation_required':True,
                    'material_parameters_verified_for_cutting':False,
                    'machine_commands_sent':False,'machining_ready':False}

    def import_model(self, path: str, units: str = 'mm', allow_duplicate: bool = False) -> dict:
        """Import through the managed native file chooser, save and verify new geometry."""
        with self.lock:
            self._require_managed()
            source = Path(path).resolve(strict=True)
            if source.suffix.lower() not in ('.stl','.svg') or not source.is_file():
                raise ValueError('Only STL and SVG imports are currently verified')
            if units != 'mm' or not isinstance(allow_duplicate,bool):
                raise ValueError('Import units must explicitly be mm and allow_duplicate must be boolean')
            if not 0 < source.stat().st_size <= 64*1024*1024:
                raise ValueError('Expected a nonempty model no larger than 64 MiB')
            if self.pending_tabs:
                raise RuntimeError('Commit or reopen pending contour details before import')
            identity = {'path':str(source),'bytes':source.stat().st_size,'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'declared_units':units}
            baseline = self.save_project()
            if not baseline.get('saved'):
                raise RuntimeError('Cannot establish saved import baseline')
            before = baseline['project']
            old_svg = {v['uuid'] for v in before['svg_geometry']}
            old_models = {uid for m in before.get('models_3d',[]) for uid in m.get('list_meshes_uuids',[])}
            ledger_path = self.managed_project.with_suffix('.imports.json')
            ledger = json.loads(ledger_path.read_text(encoding='utf-8')) if ledger_path.exists() else []
            for record in ledger:
                ids = set(record.get('native_geometry_ids',[]))
                if not allow_duplicate and record.get('sha256')==identity['sha256'] and ids and ids <= old_svg|old_models:
                    return {'imported':True,'outcome':'already_present','source':identity,'native_geometry_ids':sorted(ids),'native_mutation_performed':False,'machine_commands_sent':False,'machining_ready':False}
            if self._call('path_summary')['modal_type']:
                raise RuntimeError('Resolve the current dialog before importing')
            if not self._call('open_model_import')['queued']:
                raise RuntimeError('Native import action rejected')
            self._wait_modal('QFileDialog')
            try:
                selection = self._call('select_import_file',str(source))
            except Exception:
                if self._call('path_summary')['modal_type']=='QFileDialog':
                    self._call('cancel_managed_file_dialog')
                raise
            if not selection.get('queued'):
                raise RuntimeError('Native file selection was not accepted')
            deadline = time.monotonic()+30
            last = before
            while time.monotonic() < deadline:
                modal = self._call('path_summary')['modal_type']
                if modal:
                    if modal=='QProgressDialog':
                        time.sleep(.1)
                        continue
                    return {'imported':False,'outcome':'waiting_for_native_dialog','modal_type':modal,'source':identity,'machining_ready':False}
                saved = self.save_project()
                if saved.get('saved'):
                    last = saved['project']
                    new_svg = [v for v in last['svg_geometry'] if v['uuid'] not in old_svg]
                    new_models = [m for m in last.get('models_3d',[]) if set(m.get('list_meshes_uuids',[]))-old_models]
                    found = bool(new_svg) if source.suffix.lower()=='.svg' else any(m.get('name')==source.name and m.get('geometry_dimensions_verified') for m in new_models)
                    if found:
                        if hashlib.sha256(source.read_bytes()).hexdigest()!=identity['sha256']:
                            return {'imported':False,'outcome':'source_changed_during_import','project':last,'machining_ready':False}
                        ids = [v['uuid'] for v in new_svg]+[uid for m in new_models for uid in m.get('list_meshes_uuids',[]) if uid not in old_models]
                        ledger.append({**identity,'native_geometry_ids':ids})
                        ledger_path.write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
                        return {'imported':True,'saved_verified':True,'source':identity,'native_geometry_ids':ids,
                                'new_svg_geometry':new_svg,'new_models_3d':new_models,'project':last,
                                'placement_is_not_machine_origin':True,'toolpaths_generated':False,
                                'machine_commands_sent':False,'machining_ready':False}
                time.sleep(.1)
            return {'imported':False,'outcome':'import_not_verified_before_timeout','source':identity,'last_saved_project':last,'machining_ready':False}

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

    def set_operation_depths(self, operation_index: int, start_depth_mm: float, end_depth_mm: float, through_cut_allowance_mm: float = 0) -> dict:
        """Commit an explicit planar depth interval; require fresh calculation afterward."""
        with self.lock:
            self._require_managed()
            if isinstance(operation_index, bool) or not isinstance(operation_index, int) or operation_index < 0:
                raise ValueError('Expected a nonnegative operation index')
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                   for v in (start_depth_mm, end_depth_mm)) or not 0 <= start_depth_mm < end_depth_mm <= 10:
                raise ValueError('Expected 0 <= start depth < end depth <= 10 mm')
            if isinstance(through_cut_allowance_mm, bool) or not isinstance(through_cut_allowance_mm, (int, float)) or not math.isfinite(through_cut_allowance_mm) or not 0 <= through_cut_allowance_mm <= 2:
                raise ValueError('Expected explicit through-cut allowance between 0 and 2 mm')
            baseline = inspect_project(str(self.managed_project))
            if operation_index >= len(baseline['operations']) or baseline['operations'][operation_index]['type'] not in (0, 1, 2):
                raise ValueError('Expected an existing planar pocket/contour/drilling operation')
            if end_depth_mm > baseline['stock']['height'] + through_cut_allowance_mm + 1e-9:
                raise ValueError('Depth exceeds stock height plus explicitly permitted allowance')
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
                    'through_cut_allowance_mm': through_cut_allowance_mm,
                    'recalculation_required': True, 'simulation_verified': False,
                    'machine_commands_sent': False, 'machining_ready': False}

    def configure_contour_tabs(self, operation_index: int, count: int, width_mm: float, height_mm: float, shape: str = 'rectangle') -> dict:
        """Stage automatic tabs in native details. Generation is required to commit."""
        with self.lock:
            self._require_managed()
            if isinstance(operation_index, bool) or not isinstance(operation_index, int) or operation_index < 0:
                raise ValueError('Expected nonnegative contour operation index')
            if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
                raise ValueError('Expected tab count between 1 and 100')
            if shape not in ('rectangle', 'triangle') or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 < v <= 10 for v in (width_mm, height_mm)):
                raise ValueError('Invalid tab geometry')
            baseline = inspect_project(str(self.managed_project))
            if operation_index >= len(baseline['operations']) or baseline['operations'][operation_index]['type'] != 1:
                raise ValueError('Tabs require an existing 2D contour operation')
            op = baseline['operations'][operation_index]
            depth = op.get('depth') or {}
            if height_mm >= depth.get('endDepth', 0) - depth.get('startDepth', 0):
                raise ValueError('Tab height must be smaller than the contour cutting interval')
            if self.pending_tabs:
                raise RuntimeError('Commit or reopen the project before staging another tab change')
            live = self._call('path_summary')
            if live['modal_type'] or len(live['paths']) != len(baseline['operations']):
                raise RuntimeError('Dialog active or live operation order differs')
            if not self._call('open_operation_details', operation_index)['queued']:
                raise RuntimeError('Native detail editor did not open')
            result = self._call('configure_contour_tabs', count, width_mm, height_mm, shape)
            expected = {'supportTabs': 1, 'tabsAutoNums': count, 'tabsWidth': width_mm,
                        'tabsHeight': height_mm, 'tabsShape': 1 if shape == 'rectangle' else 0, 'tabsLayout': 1}
            requested = {'lineEdit_tabs_width': width_mm, 'lineEdit_tabs_height': height_mm, 'lineEdit_nums': count}
            if not all(result['invoked']) or any(abs(result['readback'].get(k, -1)-v) > 1e-6 for k,v in requested.items()):
                raise RuntimeError('Native tab editor values did not match')
            self.pending_tabs[operation_index] = {'native_operation_id': op['id'], 'expected': expected}
            return {'verified': True, 'verification_scope': 'native editor values only',
                    'expected_saved_tabs_after_generation': expected, 'operation_index': operation_index,
                    'saved_verified': False, 'parameter_commit_stage': 'generate_studio_toolpath',
                    'tab_positions_verified': False, 'machine_commands_sent': False, 'machining_ready': False}

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
            tab_pending = self.pending_tabs.get(operation_index)
            if self.pending_tabs and not tab_pending:
                raise RuntimeError('Another operation has uncommitted contour details')
            if tab_pending and tab_pending['native_operation_id'] != operation.get('id'):
                raise RuntimeError('Pending tabs belong to a different native operation')
            if not self._call('calculate_contour_details' if tab_pending else 'generate_path', *([] if tab_pending else [operation_index]))['queued']:
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
                    tabs_match = not tab_pending or all(isinstance((last.get('tab_settings') or {}).get(key), (int,float)) and abs(last['tab_settings'][key]-value)<1e-6 for key,value in tab_pending['expected'].items())
                    if last['calculation_evidence_verified'] and ids != old_ids and tabs_match and tab_pending and not tool_matches:
                        self.pending_tabs.pop(operation_index,None)
                        return {'verified': False, 'outcome': 'tabs_saved_but_tool_parameters_need_reselection',
                                'tab_settings_saved_verified': True, 'tab_positions_verified': False,
                                'operation': last, 'expected_tool': pending,
                                'required_action': 'Reselect the intended tool, regenerate from the path list, then verify saved parameters',
                                'simulation_verified': False, 'machining_ready': False, 'machine_commands_sent': False}
                    if last['calculation_evidence_verified'] and (not old_ids or ids != old_ids) and tool_matches and tabs_match:
                        self.pending_tools.pop(operation_index,None)
                        self.pending_tabs.pop(operation_index,None)
                        return {'verified': True, 'operation': last, 'project_path': str(self.managed_project),
                                'tab_settings_saved_verified': bool(tab_pending), 'tab_positions_verified': False,
                                'requires_tool_reselection_after_detail_commit': bool(tab_pending),
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
                self.pending_tabs = {}
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
