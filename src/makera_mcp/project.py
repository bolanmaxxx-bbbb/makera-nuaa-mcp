from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import zipfile
from pathlib import Path


def scene_vertices(scene: bytes, uuid: str, kind: str) -> dict:
    """Bounded reader for the locally verified scene_v3 MSVC archive layout.

    These are serialized render vertices, not a postprocessed NC program.
    """
    if scene[:12] != struct.pack('<I', 8) + b'scene_v3':
        raise ValueError('Unsupported native scene layout')
    marker = struct.pack('<I', len(kind)) + kind.encode('ascii') + struct.pack('<I', 36) + uuid.encode('ascii')
    if scene.count(marker) != 1:
        raise ValueError('Expected one matching native geometry record')
    end = scene.index(marker) + len(marker)
    if end + 102 > len(scene):
        raise ValueError('Truncated native geometry header')
    length = struct.unpack_from('<I', scene, end + 98)[0]
    if length == 0 or length % 12 or length > 16 * 1024 * 1024 or end + 102 + length > len(scene):
        raise ValueError('Invalid native vertex buffer length')
    values = struct.unpack_from('<' + 'f' * (length // 4), scene, end + 102)
    if not all(math.isfinite(v) for v in values):
        raise ValueError('Nonfinite native vertex buffer')
    z_levels = sorted(set(values[2::3]))
    return {'uuid': uuid, 'kind': kind, 'vertex_count': length // 12,
            'z_levels_mm': z_levels if len(z_levels) <= 256 else None,
            'z_vertex_sequence_mm': list(values[2::3]) if len(values)//3 <= 256 else None,
            'bounds_in_serialized_coordinates_mm': {axis: [min(values[i::3]), max(values[i::3])] for i, axis in enumerate('xyz')},
            'source': 'saved scene_v3 vertex buffer; local adapter layout', 'nc_export_verified': False}


def project_paths(path: str) -> list:
    with zipfile.ZipFile(path) as archive:
        project = json.loads(archive.read('makera.prj'))['project']
    return [p for coordinate in (project.get('lstCoordinate') or {}).values() for p in coordinate.get('path') or []]


def inspect_project(path: str) -> dict:
    """Read only native MKS metadata; never infer solved toolpaths from geometry."""
    target = Path(path).resolve(strict=True)
    if target.suffix.lower() != ".mks" or not zipfile.is_zipfile(target):
        raise ValueError("Expected a native .mks project archive")
    with zipfile.ZipFile(target) as archive:
        item = archive.getinfo("makera.prj")
        if item.file_size > 16 * 1024 * 1024:
            raise ValueError("Project metadata is too large")
        project = json.loads(archive.read(item).decode("utf-8"))["project"]
        coordinates = project.get("lstCoordinate") or {}
        path_entries = sum(len(item.get("path") or []) for item in coordinates.values())
        geometry_layers = [image.get("name") for item in coordinates.values() for image in item.get("images") or []]
        stock = project.get("stStockInfo") or {}
        scene_info = archive.getinfo('makera.bin')
        if scene_info.file_size > 64 * 1024 * 1024:
            raise ValueError('Native scene is too large for this adapter')
        scene = archive.read('makera.bin')
        svg_geometry = []
        for match in re.finditer(rb'\x08\x00\x00\x00SvgShape\x24\x00\x00\x00([A-Za-z0-9-]{36})', scene):
            try:
                svg_geometry.append(scene_vertices(scene, match.group(1).decode('ascii'), 'SvgShape'))
            except ValueError:
                pass
        operations = []
        for coordinate_name, coordinate in coordinates.items():
            for native_path in coordinate.get('path') or []:
                mesh_ids = [uid for ids in native_path.get('gcodemeshuuidmap', {}).values() for uid in ids]
                meshes, auxiliary, errors = [], [], []
                for uid in mesh_ids:
                    try:
                        meshes.append(scene_vertices(scene, uid, 'gcode_polyline'))
                    except ValueError as error:
                        # Drilling also links a hole-preview mesh whose serialized
                        # node name is its UUID. It is geometry, not a motion path.
                        if native_path.get('type') == 2:
                            try:
                                auxiliary.append(scene_vertices(scene, uid, uid))
                            except ValueError:
                                errors.append(str(error))
                        else:
                            errors.append(str(error))
                operations.append({'index': len(operations), 'coordinate': coordinate_name,
                    'id': native_path.get('id'), 'name': native_path.get('pathName'), 'type': native_path.get('type'),
                    'native_state': native_path.get('emPathState'), 'geometry_uuids': native_path.get('selectedmeshes', []),
                    'depth': native_path.get('depth'), 'tools': native_path.get('tools', []),
                    'tab_settings': native_path.get('tab'),
                    'support_tab_list': native_path.get('support_tab_list', []),
                    'support_mesh_ids': native_path.get('supportmeshesuuid', []),
                    'drilling_retract': {key:native_path[key] for key in ('retract_distance','retract_position','retract_z') if key in native_path},
                    'estimated_time_s': native_path.get('processingTime'), 'toolpath_meshes': meshes,
                    'auxiliary_geometry_meshes': auxiliary, 'mesh_read_errors': errors,
                    'calculation_evidence_verified': native_path.get('emPathState') == 1 and bool(meshes) and len(meshes)+len(auxiliary) == len(mesh_ids) and not errors})
        return {"path": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "project_name": project.get("strProjectName"), "machine_profile": project.get("machine"),
                "stock": {key: stock.get(key) for key in ("length", "width", "height", "material", "materialobj", "materialspec")},
                "geometry_layers": geometry_layers, "native_path_entries": path_entries,
                "svg_geometry": svg_geometry, "operations": operations,
                "verified_calculated_operations": sum(o['calculation_evidence_verified'] for o in operations),
                "native_toolpath_counter": project.get("nToolPathesMaxNum"),
                "geometry_only_detected": path_entries == 0 and project.get("nToolPathesMaxNum", 0) == 0,
                "native_archive_entries": archive.namelist(),
                "simulation_verified": False,
                "limitations": ["MKS field meanings verified only against this local draft; no public schema assumed",
                                "Path entries/counters do not prove successful calculation or safe machining",
                                "stModelInfo values are not used as validated geometry dimensions",
                                "Saved machine profile is not proof of live device model or ATC capability"]}
