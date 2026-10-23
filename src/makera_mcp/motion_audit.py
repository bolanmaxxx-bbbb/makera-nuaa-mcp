"""Read-only native display-path diagnostics; not stock removal or an NC simulator."""
from __future__ import annotations

import math
import hashlib
from pathlib import Path
import struct
import zipfile

from .project import inspect_project


def audit_segments(segments, tool_diameter_mm: float) -> dict:
    if (isinstance(tool_diameter_mm, bool) or not isinstance(tool_diameter_mm, (int,float))
            or not math.isfinite(tool_diameter_mm) or tool_diameter_mm <= 0):
        raise ValueError('Expected a positive finite tool diameter')
    total = long_length = 0.0
    long_legs = []
    count = 0
    for a, b in segments:
        if len(a) != 3 or len(b) != 3 or not all(math.isfinite(v) for v in (*a,*b)):
            raise ValueError('Expected finite XYZ segment endpoints')
        count += 1
        length = math.dist(a,b)
        total += length
        xy = math.hypot(b[0]-a[0], b[1]-a[1])
        if xy >= max(1.0, 2*tool_diameter_mm) and abs(b[2]-a[2]) < 1e-4:
            long_length += xy
            long_legs.append((a,b,xy))
    same_layer = changed_depth = 0
    examples = []
    for previous, current in zip(long_legs, long_legs[1:]):
        a,b,length = previous
        c,d,next_length = current
        u = [(b[i]-a[i])/length for i in range(2)]
        v = [(d[i]-c[i])/next_length for i in range(2)]
        if sum(u[i]*v[i] for i in range(2)) > -.999 or abs(length-next_length) > max(.01,length*.01):
            continue
        lateral = abs((c[0]-a[0])*u[1]-(c[1]-a[1])*u[0])
        axial = abs(sum((d[i]-a[i])*u[i] for i in range(2)))
        if lateral >= tool_diameter_mm or axial > max(.01,length*.01):
            continue
        z_change = c[2]-a[2]
        if abs(z_change) < 1e-4:
            same_layer += 1
            if len(examples) < 12:
                examples.append({'length_mm':length,'z_mm':a[2],
                                 'lateral_separation_mm':lateral,
                                 'long_strip_overlap_fraction_estimate':max(0,1-lateral/tool_diameter_mm)})
        else:
            changed_depth += 1
    return {'segment_count':count,'display_motion_length_mm':total,
            'long_horizontal_leg_count':len(long_legs),'long_horizontal_length_mm':long_length,
            'same_layer_opposite_leg_pairs':same_layer,'opposite_leg_pairs_with_depth_change':changed_depth,
            'same_layer_examples':examples,'efficiency_review_required':same_layer > 0,
            'actual_air_cut_length_verified':False,'bidirectional_material_removal_verified':False,
            'limitations':['Geometric repeated coverage is a review signal, not proof of zero material removal',
                           'Native GL_LINES buffers do not identify G0/G1, actual feed, rapid/retract timing or remaining stock',
                           'Do not force reciprocal cuts without considering climb/conventional direction, engagement, tabs and chip evacuation']}


def audit_saved_toolpath(path: str, operation_index: int) -> dict:
    snapshot = inspect_project(path)
    if type(operation_index) is not int or not 0 <= operation_index < len(snapshot['operations']):
        raise ValueError('Expected a saved operation index')
    op = snapshot['operations'][operation_index]
    if len(op['tools']) != 1:
        raise ValueError('Expected exactly one saved tool')
    if not op['toolpath_meshes']:
        raise ValueError('Operation has no saved motion buffers')
    with zipfile.ZipFile(snapshot['path']) as archive:
        scene = archive.read('makera.bin')
    if hashlib.sha256(Path(snapshot['path']).read_bytes()).hexdigest() != snapshot['sha256']:
        raise ValueError('Project changed while reading motion; refresh the snapshot')
    segments = []
    for mesh in op['toolpath_meshes']:
        kind = b'gcode_polyline'
        marker = struct.pack('<I',len(kind))+kind+struct.pack('<I',36)+mesh['uuid'].encode('ascii')
        if scene.count(marker) != 1:
            raise ValueError('Expected one saved motion node')
        offset = scene.index(marker)+len(marker)
        length = struct.unpack_from('<I',scene,offset+98)[0]
        if not length or length%24 or offset+102+length > len(scene):
            raise ValueError('Invalid native GL_LINES buffer')
        matrix = mesh['node_matrix_column_major']
        points = [tuple(sum(matrix[row+4*col]*p[col] for col in range(3))+matrix[12+row]
                        for row in range(3))
                  for p in struct.iter_unpack('<fff',scene[offset+102:offset+102+length])]
        segments.extend(zip(points[::2],points[1::2]))
    result = audit_segments(segments,op['tools'][0]['diameter'])
    result.update(project_path=snapshot['path'],project_sha256=snapshot['sha256'],
                  operation_index=operation_index,operation_id=op['id'],
                  saved_calculation_evidence_verified=op['calculation_evidence_verified'],
                  saved_depth=op['depth'],nc_terminal_state_verified=False,machine_commands_sent=False)
    return result
