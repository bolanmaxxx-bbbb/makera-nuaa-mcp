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
from .operation_catalog import operation_catalog


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
        self.pending_chamfer = {}

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
                    self.pending_chamfer = {}

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

    def place_model(self, model_uuid: str, center_mm: dict, project_sha256: str, world_rotation_deg: dict | None = None) -> dict:
        """Place one single-mesh model in the serialized stock frame, with saved evidence."""
        with self.lock:
            self._require_managed()
            def vector(value, label):
                if not isinstance(value,dict) or set(value)!={'x','y','z'} or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in value.values()):
                    raise ValueError(f'{label} requires finite x/y/z values')
                return [value[k] for k in 'xyz']
            center = vector(center_mm, 'center_mm')
            rotation = vector(world_rotation_deg if world_rotation_deg is not None else dict.fromkeys('xyz',0), 'world_rotation_deg')
            if sum(v!=0 for v in rotation)>1 or any(abs(v)>180 or abs(v/90-round(v/90))>1e-9 for v in rotation):
                raise ValueError('Only one world axis and explicit 90-degree increments within -180..180 are verified')
            if not isinstance(model_uuid,str) or not re.fullmatch(r'[A-Za-z0-9-]{36}',model_uuid) or not isinstance(project_sha256,str) or not re.fullmatch(r'[a-f0-9]{64}',project_sha256):
                raise ValueError('Expected the current native mesh UUID and project SHA256')
            if self.pending_tabs or self.pending_chamfer or self.pending_tools:
                raise RuntimeError('Commit or reopen pending CAM settings before model placement')
            current = inspect_project(str(self.managed_project))
            if current['sha256'] != project_sha256:
                raise ValueError('Project changed; refresh the snapshot before model placement')
            models = current.get('models_3d',[])
            if len(models)!=1 or len(models[0].get('meshes',[]))!=1 or not models[0].get('geometry_dimensions_verified') or models[0]['meshes'][0]['uuid']!=model_uuid:
                raise ValueError('Placement currently requires exactly one verified 3D model with one mesh')
            model = models[0];mesh = model['meshes'][0];stock = current['stock']
            if stock.get('StockShape')!=1 or any(not isinstance(stock.get(k),(int,float)) or not math.isfinite(stock[k]) or stock[k]<=0 for k in ('length','width','height')):
                raise ValueError('Model placement currently requires verified rectangular stock')
            if isinstance(model.get('id'),bool) or not isinstance(model.get('id'),int) or model['id']<0:
                raise ValueError('Native model ID is missing')
            matrix = mesh['matrix_column_major']
            linear = [[matrix[row+4*col] for col in range(3)] for row in range(3)]
            determinant = (linear[0][0]*(linear[1][1]*linear[2][2]-linear[1][2]*linear[2][1])
                           -linear[0][1]*(linear[1][0]*linear[2][2]-linear[1][2]*linear[2][0])
                           +linear[0][2]*(linear[1][0]*linear[2][1]-linear[1][1]*linear[2][0]))
            if (any(abs(v-round(v))>1e-5 or abs(v)>1+1e-5 for row in linear for v in row)
                    or any(abs(sum(abs(v) for v in row)-1)>1e-5 for row in linear)
                    or any(abs(sum(abs(linear[r][c]) for r in range(3))-1)>1e-5 for c in range(3))
                    or abs(determinant-1)>1e-5):
                raise ValueError('Existing model transform must be unscaled and aligned to stock axes')
            axis = next((i for i,v in enumerate(rotation) if v),0)
            angle = math.radians(rotation[axis]);c,s = math.cos(angle),math.sin(angle)
            turns = [((1,0,0),(0,c,-s),(0,s,c)),((c,0,s),(0,1,0),(-s,0,c)),((c,-s,0),(s,c,0),(0,0,1))]
            expected_linear = [[sum(turns[axis][row][k]*matrix[k+4*col] for k in range(3)) for col in range(3)] for row in range(3)]
            local = mesh['bounds_in_serialized_coordinates_mm']
            corners = [(x,y,z) for x in local['x'] for y in local['y'] for z in local['z']]
            rotated = [tuple(sum(expected_linear[r][k]*p[k] for k in range(3)) for r in range(3)) for p in corners]
            expected_size = [max(p[i] for p in rotated)-min(p[i] for p in rotated) for i in range(3)]
            limits = [(0,stock['length']),(0,stock['width']),(-stock['height'],0)]
            if any(center[i]-expected_size[i]/2<lo-1e-4 or center[i]+expected_size[i]/2>hi+1e-4 for i,(lo,hi) in enumerate(limits)):
                raise ValueError('Requested model bounding box extends outside the stock')
            if self._call('path_summary')['modal_type']:
                raise RuntimeError('Resolve the current dialog before model placement')
            baseline = self.save_project()
            if not baseline.get('saved'):
                raise RuntimeError('Cannot establish a saved placement baseline')
            # Unsaved edits must be reviewed as a new snapshot, not silently consumed.
            if any(baseline['project'].get(k)!=current.get(k) for k in ('stock','svg_geometry','models_3d','operations')):
                raise RuntimeError('Unsaved project changes were saved; refresh the placement snapshot')
            native = self._call('place_single_model',model['id'],center,rotation)
            saved = self.save_project()
            after = saved.get('project',{})
            updated = next((m for m in after.get('models_3d',[]) if m.get('id')==model['id']),{})
            placed = next((m for m in updated.get('meshes',[]) if m.get('uuid')==model_uuid),{})
            actual_matrix = placed.get('matrix_column_major',[])
            pose_matches = len(actual_matrix)==16 and all(abs(actual_matrix[row+4*col]-expected_linear[row][col])<1e-4 for row in range(3) for col in range(3)) and all(abs(placed.get('center_after_node_transform_mm',{}).get(k,float('inf'))-center[i])<1e-4 for i,k in enumerate('xyz'))
            untouched = (after.get('svg_geometry')==current['svg_geometry'] and after.get('stock')==current['stock']
                         and len(after.get('models_3d',[]))==1 and len(updated.get('meshes',[]))==1
                         and placed.get('dimensions_mm')==mesh['dimensions_mm'] and placed.get('vertex_count')==mesh['vertex_count'])
            op_keys = ('id','type','geometry_uuids','tools','depth','tab_settings','relief_settings','chamfer_settings')
            untouched = untouched and [{k:o.get(k) for k in op_keys} for o in after.get('operations',[])]==[{k:o.get(k) for k in op_keys} for o in current['operations']]
            return {'verified':bool(native.get('invoked') and saved.get('saved') and pose_matches and untouched),
                    'native_result':native,'model':updated,'unrelated_inputs_unchanged':untouched,
                    'project':after,'recalculation_required':True,'placement_frame':'saved model node / nominal stock frame; not machine coordinates',
                    'rotation_is_relative':True,'retry_requires_fresh_snapshot':True,
                    'simulation_verified':False,'machining_ready':False,'machine_commands_sent':False}

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
            if self.pending_tabs or self.pending_chamfer:
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
            if self.pending_tabs or self.pending_chamfer:
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
            if self.pending_tabs or self.pending_chamfer:
                raise RuntimeError('Commit or reopen pending detail edits before editing the library')
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
            snapshot = inspect_project(str(self.managed_project))
            if type(operation_index) is not int or not 0 <= operation_index < len(snapshot['operations']):
                raise ValueError('Expected an existing operation index')
            expected_kind = {0:'平底刀',1:'平底刀',2:'钻头',6:'平底刀',13:'V刀'}.get(snapshot['operations'][operation_index]['type'])
            if expected_kind is None:
                raise ValueError('Tool selection for this operation is not adapted')
            if self.pending_tabs or self.pending_chamfer:
                raise RuntimeError('Commit or reopen pending detail edits before selecting a tool')
            self._wait_modal(None)
            self._call('open_tool_editor', operation_index)
            self._wait_modal('CutterLibWgt')
            try:
                self._call('select_tool_group', group)
                selected = self._call('select_library_tool', name, material)
                if not selected['material_selected']:
                    raise RuntimeError('Requested tool material parameters are missing')
                tables = self._call('tool_tables')
                info = next(t for t in tables if t['name']=='tableWidget_tool_infomation')
                values = {re.split(r'[（(]',header)[0].strip():row[0] for header,row in zip(info['row_labels'],info['values']) if header}
                if values.get('类型') != expected_kind:
                    raise ValueError('Selected tool type does not match the operation')
                rate_rows = next(t for t in tables if t['name']=='tableWidget_material')['values']
                rate = next(row for row in rate_rows if row[0]==material)
                expected = {'toolName':name,'diameter':float(values['直径']),'fluteLength':float(values['刃长']),
                            'handleDiameter':float(values['刀柄直径']), 'spindleSpeed':float(rate[1]),
                            'feedrate':float(rate[2]),'plungeFeedrate':float(rate[3]),'stepDown':float(rate[4]),'stepOverMM':float(rate[5])}
                if expected_kind == 'V刀':
                    # The V information table omits tip diameter. Read the native
                    # geometry editor without changing/saving it, then verify the
                    # values again in the calculated project.
                    self._call('tool_library_action','edit_tool')
                    self._wait_modal('CutterEditDlg')
                    try:
                        geometry = self._call('read_v_tool_geometry')
                    finally:
                        if self._call('path_summary')['modal_type'] == 'CutterEditDlg':
                            self._call('cancel_tool_edit')
                            self._wait_modal('CutterLibWgt')
                    expected.update(toolType=2, **geometry)
                    self._validate_chamfer_tool(expected)
                self._call('tool_library_action', 'choose')
            except Exception:
                # Reject a wrong cutter without leaving its chooser blocking the next request.
                if self._call('path_summary')['modal_type'] == 'CutterLibWgt':
                    self._call('tool_library_action', 'close')
                    self._wait_modal(None)
                raise
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

    def create_chamfer(self, geometry_uuids: list[str], chamfer_depth_mm: float) -> dict:
        return self._create_2d(geometry_uuids, chamfer_depth_mm, 'chamfer')

    @staticmethod
    def _validate_chamfer_tool(tool):
        positive = ('diameter','handleDiameter','fluteLength','spindleSpeed','feedrate','plungeFeedrate','stepDown','stepOverMM','angle')
        if not isinstance(tool.get('toolName'),str) or not tool['toolName'].strip() or tool.get('toolType') != 2 or any(isinstance(tool.get(k),bool) or not isinstance(tool.get(k),(int,float))
                or not math.isfinite(tool[k]) or tool[k] < 1e-6 for k in positive):
            raise ValueError('Chamfer requires a verified V tool and explicit positive cutting parameters')
        tip = tool.get('tipDiameter')
        if isinstance(tip,bool) or not isinstance(tip,(int,float)) or not math.isfinite(tip) or not 0 <= tip < tool['diameter'] or tool['angle'] >= 180:
            raise ValueError('Invalid V tool angle or tip diameter')
        if abs(tool['angle']-90)>1e-6:
            raise ValueError('Current verified chamfer scope requires a 90-degree V tool')
        if tool['stepDown'] >= tool['fluteLength'] or tool['stepOverMM'] > tool['diameter']:
            raise ValueError('Chamfer tool increments exceed geometry')

    def _chamfer_preflight(self, snapshot, index, start, chamfer_depth, tip_offset, side, direction):
        if type(index) is not int or not 0 <= index < len(snapshot['operations']) or snapshot['operations'][index]['type'] != 13:
            raise ValueError('Expected an existing 2D chamfer operation')
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in (start,chamfer_depth,tip_offset)) or not 0 <= start <= 10 or not 0 < chamfer_depth <= 10 or not 0 <= tip_offset <= 2:
            raise ValueError('Expected nonnegative start, positive chamfer depth, and 0..2 mm tip offset')
        if side not in ('inside','outside') or direction not in ('climb','conventional'):
            raise ValueError('Explicit inside/outside and climb/conventional required')
        op = snapshot['operations'][index]
        known = {item['uuid'] for item in snapshot['svg_geometry']}
        if not op['geometry_uuids'] or any(uid not in known for uid in op['geometry_uuids']):
            raise ValueError('Chamfer requires current saved SVG geometry')
        tool = self.pending_tools.get(index) or (op['tools'][0] if len(op['tools']) == 1 else {})
        self._validate_chamfer_tool(tool)
        cone_length = (tool['diameter']-tool['tipDiameter']) / (2*math.tan(math.radians(tool['angle']/2)))
        # Native endDepth means chamfer SIZE here, not an absolute end depth.
        tip_depth = start+chamfer_depth+tip_offset
        if tip_depth > min(10,snapshot['stock']['height'],tool['fluteLength'])+1e-8 or chamfer_depth+tip_offset > cone_length+1e-8:
            raise ValueError('Chamfer tip depth exceeds stock or usable V cutting geometry')
        return op

    def configure_chamfer(self, operation_index: int, start_depth_mm: float, chamfer_depth_mm: float,
                          tip_offset_mm: float, side: str, direction: str, project_sha256: str) -> dict:
        """Stage explicit native chamfer details; generation commits and verifies them."""
        with self.lock:
            self._require_managed()
            snapshot = inspect_project(str(self.managed_project))
            if not isinstance(project_sha256,str) or snapshot['sha256'] != project_sha256:
                raise ValueError('Fresh saved project SHA256 required')
            op = self._chamfer_preflight(snapshot,operation_index,start_depth_mm,chamfer_depth_mm,tip_offset_mm,side,direction)
            if self.pending_tabs or self.pending_chamfer or any(i != operation_index for i in self.pending_tools):
                raise RuntimeError('Commit or reopen other pending CAM edits first')
            live = self._call('path_summary')
            if live['modal_type'] or len(live['paths']) != len(snapshot['operations']):
                raise RuntimeError('Resolve dialogs or unsaved operation order before chamfer editing')
            if not self._call('open_operation_details',operation_index)['queued']:
                raise RuntimeError('Cannot open chamfer details')
            result = self._call('configure_chamfer_details',start_depth_mm,chamfer_depth_mm,tip_offset_mm,side,direction)
            requested = {'lineEdit_start_depth_2':start_depth_mm,'lineEdit_end_depth_2':chamfer_depth_mm,'lineEdit_finish_2':tip_offset_mm}
            if not all(result['invoked']) or not result.get('choices_checked') or any(abs(result['readback'].get(k,-1)-v)>1e-6 for k,v in requested.items()):
                raise RuntimeError('Chamfer native editor values did not match; inspect before retrying')
            expected = {'chamferTipOffset':tip_offset_mm,'contourPosition':1 if side=='inside' else 0,'workDirection':1 if direction=='climb' else 0}
            self.pending_chamfer[operation_index] = {'native_operation_id':op['id'],'geometry_uuids':op['geometry_uuids'],
                'depth':{'startDepth':start_depth_mm,'endDepth':chamfer_depth_mm},'expected':expected,'side':side,'direction':direction,
                'project_sha256':snapshot['sha256']}
            if operation_index not in self.pending_tools:
                # A detail revision can overwrite even a previously saved tool.
                # Preserve its verified cutting fields so that a new motion mesh
                # cannot conceal that overwrite when no reselection was staged.
                self.pending_tools[operation_index] = {key:op['tools'][0][key] for key in
                    ('toolName','toolType','diameter','handleDiameter','fluteLength','angle','tipDiameter',
                     'spindleSpeed','feedrate','plungeFeedrate','stepDown','stepOverMM')}
            return {'verified':True,'verification_scope':'native editor values only','saved_verified':False,
                    'operation_index':operation_index,'expected_saved_chamfer_after_generation':expected,
                    'parameter_commit_stage':'generate_studio_toolpath','recalculation_required':True,
                    'native_endDepth_meaning':'chamfer depth, not absolute end Z','tip_depth_mm':start_depth_mm+chamfer_depth_mm+tip_offset_mm,
                    'machine_commands_sent':False,'machining_ready':False}

    def operation_catalog(self) -> dict:
        with self.lock:
            self._require_managed()
            baseline = inspect_project(str(self.managed_project))['sha256']
            self._wait_modal(None)
            if not self._call('open_path_chooser')['queued']:
                raise RuntimeError('Cannot read native chooser catalog')
            self._wait_modal('AddKnifeTypeDlg')
            try:
                labels = self._call('path_type_options')
            finally:
                if self._call('path_summary')['modal_type'] == 'AddKnifeTypeDlg':
                    self._call('cancel_path_chooser')
                    self._wait_modal(None)
            if inspect_project(str(self.managed_project))['sha256'] != baseline:
                raise RuntimeError('Project unexpectedly changed during catalog read')
            return {**operation_catalog(labels),'saved_project_unchanged':True}

    def _relief_preflight(self, model_uuid, layered, allowance_mm, project_sha256, allow_pending_tool=False):
        self._require_managed()
        if not isinstance(model_uuid,str) or not re.fullmatch(r'[A-Za-z0-9-]{36}',model_uuid) or not isinstance(project_sha256,str) or not re.fullmatch(r'[a-f0-9]{64}',project_sha256):
            raise ValueError('Expected current model mesh UUID and project SHA256')
        if type(layered) is not bool or isinstance(allowance_mm, bool) or not isinstance(allowance_mm, (int,float)) or not math.isfinite(allowance_mm) or not 0 <= allowance_mm <= 2:
            raise ValueError('Explicit layered boolean and finite 0..2 mm allowance required')
        if (self.pending_tools and not allow_pending_tool) or self.pending_tabs or self.pending_chamfer:
            raise RuntimeError('Commit or resolve pending CAM edits first')
        snapshot = inspect_project(str(self.managed_project))
        if not isinstance(project_sha256,str) or snapshot['sha256'] != project_sha256:
            raise ValueError('Fresh project SHA256 required; inspect before creating/configuring')
        meshes = [mesh for model in snapshot['models_3d'] for mesh in model['meshes']]
        if len(snapshot['models_3d']) != 1 or not snapshot['models_3d'][0].get('geometry_dimensions_verified') or len(meshes) != 1 or meshes[0]['uuid'] != model_uuid:
            raise ValueError('Relief currently requires one verified single-mesh 3D model')
        bounds = meshes[0]['bounds_after_node_transform_mm']
        stock = snapshot['stock']
        if stock['StockShape'] != 1 or any(isinstance(stock.get(k),bool) or not isinstance(stock.get(k),(int,float)) or not math.isfinite(stock[k]) or stock[k]<=0 for k in ('length','width','height')) or stock['height'] > 10:
            raise ValueError('Relief development scope: rectangular stock, thickness <=10 mm')
        for axis, low, high in (('x',0,stock['length']),('y',0,stock['width']),('z',-stock['height'],0)):
            if bounds[axis][0] < low-1e-5 or bounds[axis][1] > high+1e-5:
                raise ValueError('Transformed model must be inside the stock')
        live = self._call('path_summary')
        if live['modal_type'] or len(live['paths']) != len(snapshot['operations']):
            raise RuntimeError('Resolve dialogs/unsaved operation order before 3D editing')
        return snapshot

    def _commit_relief(self, index, model_uuid, layered, allowance_mm):
        if not self._call('show_path_list')['visible']:
            raise RuntimeError('Model preparation has not reached the native path list')
        invoked = self._call('configure_relief',index,model_uuid,layered,allowance_mm)
        if not all(invoked['invoked']):
            raise RuntimeError('Native 3D relief configuration rejected; inspect before retry')
        saved = self.save_project()
        op = saved['project']['operations'][index] if saved['saved'] else {}
        settings = op.get('relief_settings') or {}
        verified = (op.get('type') == 6 and op.get('geometry_uuids') == [model_uuid]
                    and settings.get('finishing') == [not layered]
                    and settings.get('machining_allowance') == [allowance_mm])
        return {'created_and_saved':verified,'saved_verified':verified,'operation_index':index,'operation':op,
                'model_uuid':model_uuid,'layered':layered,'allowance_mm':allowance_mm,
                'depth_basis':'transformed selected model; planar depth fields do not define relief depth',
                'calculated':False,'machining_ready':False,'machine_commands_sent':False}

    def create_relief(self, model_uuid: str, layered: bool, allowance_mm: float, project_sha256: str) -> dict:
        """Create only a native relief operation, not feature/edge/rotary operations."""
        with self.lock:
            before = self._relief_preflight(model_uuid,layered,allowance_mm,project_sha256)
            if not self._call('show_path_list')['visible']:
                raise RuntimeError('Model preparation has not reached the native path list')
            if not self._call('open_path_chooser')['queued']:
                raise RuntimeError('Cannot open native path chooser')
            self._wait_modal('AddKnifeTypeDlg')
            if not self._call('choose_path_type','3D浮雕')['queued']:
                raise RuntimeError('Cannot choose native relief')
            self._wait_modal(None)
            self._call('barrier')
            live = self._call('path_summary')
            index = len(before['operations'])
            if len(live['paths']) != index+1:
                raise RuntimeError('New relief operation count not verified; do not blindly retry')
            return self._commit_relief(index,model_uuid,layered,allowance_mm)

    def configure_relief(self, operation_index: int, layered: bool, allowance_mm: float, project_sha256: str) -> dict:
        with self.lock:
            self._require_managed()
            snapshot = inspect_project(str(self.managed_project))
            if type(operation_index) is not int or not 0 <= operation_index < len(snapshot['operations']):
                raise ValueError('Saved operation index does not exist')
            operation = snapshot['operations'][operation_index]
            if operation['type'] != 6 or len(operation['geometry_uuids']) != 1:
                raise ValueError('Expected a configured native relief operation')
            uuid = operation['geometry_uuids'][0]
            self._relief_preflight(uuid,layered,allowance_mm,project_sha256)
            return self._commit_relief(operation_index,uuid,layered,allowance_mm)

    def _create_2d(self, geometry_uuids: list[str], end_depth_mm: float, kind: str) -> dict:
        """Create a native planar operation, verify geometry and depth persistence."""
        with self.lock:
            self._require_managed()
            if self.pending_tabs or self.pending_chamfer:
                raise RuntimeError('Commit or reopen pending detail edits before creating an operation')
            types = {'pocket': (0, '2D区域清理'), 'contour': (1, '2D轮廓'), 'drilling': (2, '2D钻孔'), 'chamfer': (13, '2D倒角')}
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
            if self.pending_tabs or self.pending_chamfer:
                raise RuntimeError('Commit or reopen pending detail edits before editing depths')
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
            if self.pending_tabs or self.pending_chamfer:
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
            if operation['type'] not in (0,1,2,6,13) or not operation['geometry_uuids']:
                raise ValueError('Only configured planar pocket/contour/drilling or single-model relief operations are supported')
            if operation['type'] == 6:
                if len(operation['geometry_uuids']) != 1:
                    raise ValueError('Relief requires exactly one selected model mesh')
                models = baseline_project['models_3d']
                if len(models)!=1 or not models[0].get('geometry_dimensions_verified') or len(models[0]['meshes'])!=1 or models[0]['meshes'][0]['uuid'] != operation['geometry_uuids'][0]:
                    raise ValueError('Selected relief mesh no longer identifies the current verified model')
                pending = self.pending_tools.get(operation_index)
                tool = pending or (operation['tools'][0] if len(operation['tools']) == 1 else {})
                for field in ('diameter','fluteLength','spindleSpeed','feedrate','plungeFeedrate','stepDown','stepOverMM'):
                    value = tool.get(field)
                    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value < 1e-6:
                        raise ValueError('Explicit verified flat-tool selection/parameters required before relief generation')
                if tool['stepOverMM'] > tool['diameter'] or tool['stepDown'] >= tool['fluteLength']:
                    raise ValueError('Relief tool increments exceed geometry')
                if tool['fluteLength'] < baseline_project['stock']['height'] or (not pending and tool.get('toolType') != 1):
                    raise ValueError('Relief requires a verified flat tool with flute length covering this stock-depth scope')
                settings = operation.get('relief_settings') or {}
                if len(settings.get('finishing') or []) != 1 or type(settings['finishing'][0]) is not bool or len(settings.get('machining_allowance') or []) != 1:
                    raise ValueError('Relief has unknown layering/allowance settings')
                self._relief_preflight(operation['geometry_uuids'][0],not settings['finishing'][0],
                    settings['machining_allowance'][0],baseline_project['sha256'],allow_pending_tool=True)
            chamfer_pending = self.pending_chamfer.get(operation_index)
            if self.pending_chamfer and not chamfer_pending:
                raise RuntimeError('Another operation has uncommitted chamfer details')
            if operation['type'] == 13:
                if chamfer_pending:
                    if chamfer_pending['native_operation_id'] != operation['id'] or chamfer_pending['project_sha256'] != baseline_project['sha256']:
                        raise RuntimeError('Pending chamfer belongs to a different operation or saved revision')
                    depth,settings = chamfer_pending['depth'],chamfer_pending['expected']
                else:
                    depth,settings = operation.get('depth') or {},operation.get('chamfer_settings') or {}
                if settings.get('contourPosition') not in (0,1) or settings.get('workDirection') not in (0,1):
                    raise ValueError('Unknown saved chamfer position/direction')
                self._chamfer_preflight(baseline_project,operation_index,depth.get('startDepth'),depth.get('endDepth'),
                    settings.get('chamferTipOffset'),'inside' if settings['contourPosition']==1 else 'outside',
                    'climb' if settings['workDirection']==1 else 'conventional')
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
            calculation = 'calculate_chamfer_details' if chamfer_pending else 'calculate_contour_details' if tab_pending else 'generate_path'
            if not self._call(calculation, *([] if tab_pending or chamfer_pending else [operation_index]))['queued']:
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
                    chamfer_expected = chamfer_pending['expected'] if chamfer_pending else operation.get('chamfer_settings')
                    chamfer_depth = chamfer_pending['depth'] if chamfer_pending else operation.get('depth')
                    chamfer_matches = operation['type'] != 13 or (last.get('chamfer_settings') == chamfer_expected
                        and last.get('geometry_uuids') == operation['geometry_uuids']
                        and all(abs((last.get('depth') or {}).get(k,-1)-chamfer_depth[k])<1e-6 for k in ('startDepth','endDepth')))
                    if operation['type'] == 13 and last['calculation_evidence_verified'] and ids != old_ids and not chamfer_matches:
                        return {'verified':False,'outcome':'chamfer_inputs_changed_during_generation','operation':last,
                                'machine_commands_sent':False,'machining_ready':False}
                    if operation['type'] == 6 and last['calculation_evidence_verified'] and ids != old_ids and (
                        last.get('relief_settings') != operation.get('relief_settings')
                        or last.get('geometry_uuids') != operation.get('geometry_uuids')
                        or saved['project']['models_3d'] != baseline_project['models_3d']):
                        return {'verified':False,'outcome':'relief_inputs_changed_during_generation','operation':last,
                                'machining_ready':False,'machine_commands_sent':False}
                    if last['calculation_evidence_verified'] and ids != old_ids and tabs_match and tab_pending and not tool_matches:
                        self.pending_tabs.pop(operation_index,None)
                        return {'verified': False, 'outcome': 'tabs_saved_but_tool_parameters_need_reselection',
                                'tab_settings_saved_verified': True, 'tab_positions_verified': False,
                                'operation': last, 'expected_tool': pending,
                                'required_action': 'Reselect the intended tool, regenerate from the path list, then verify saved parameters',
                                'simulation_verified': False, 'machining_ready': False, 'machine_commands_sent': False}
                    if last['calculation_evidence_verified'] and ids != old_ids and chamfer_pending and chamfer_matches and not tool_matches:
                        self.pending_chamfer.pop(operation_index,None)
                        return {'verified':False,'outcome':'chamfer_saved_but_tool_parameters_need_reselection',
                                'chamfer_settings_saved_verified':True,'operation':last,'expected_tool':pending,
                                'required_action':'Reselect the intended V tool and regenerate from the path list, then verify all saved parameters',
                                'simulation_verified':False,'machining_ready':False,'machine_commands_sent':False}
                    if last['calculation_evidence_verified'] and chamfer_matches and (not old_ids or ids != old_ids) and tool_matches and tabs_match:
                        self.pending_tools.pop(operation_index,None)
                        self.pending_tabs.pop(operation_index,None)
                        self.pending_chamfer.pop(operation_index,None)
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
                self.pending_chamfer = {}
                return {"opened": True, "native_method": queued["method"], "path": str(project),
                        "state": state, "source": "native Qt slot + native property readback",
                        "existing_same_test_saved_before_reopen": same_project_saved}
            time.sleep(0.1)
        return {"opened": False, "outcome": "queued_but_not_verified", "state": state}

    def set_stock(self, dimensions: dict) -> dict:
        if self.pending_tabs or self.pending_chamfer:
            raise RuntimeError('Commit or reopen pending detail edits before editing stock')
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
