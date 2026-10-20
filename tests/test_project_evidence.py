import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from makera_mcp.project import inspect_project, scene_vertices

UUID = '1a1CVR13-c1aF-wNLN-ervA-Iqv341YtoRCs'


def scene(values=(1.0, 2.0, -.1, 2.0, 3.0, -.1)):
    data = struct.pack('<' + 'f' * len(values), *values)
    kind = b'gcode_polyline'
    return (struct.pack('<I', 8) + b'scene_v3' + struct.pack('<I', len(kind)) + kind +
            struct.pack('<I', 36) + UUID.encode('ascii') + b'\0' * 98 + struct.pack('<I', len(data)) + data)


class NativeCalculationEvidence(unittest.TestCase):
    def inspect(self, state, linked, binary):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'test.mks'
            with zipfile.ZipFile(target, 'w') as archive:
                archive.writestr('makera.bin', binary)
                archive.writestr('makera.prj', json.dumps({'project': {'lstCoordinate': {'0': {'path': [{
                    'emPathState': state, 'gcodemeshuuidmap': {'0': linked}}]}}}}))
            return inspect_project(str(target))

    def test_success_flag_without_native_vertices_is_not_calculated(self):
        result = self.inspect(1, [UUID], b'')
        self.assertEqual(result['verified_calculated_operations'], 0)
        self.assertTrue(result['operations'][0]['mesh_read_errors'])

    def test_pending_state_with_old_mesh_is_not_calculated(self):
        self.assertEqual(self.inspect(0, [UUID], scene())['verified_calculated_operations'], 0)

    def test_valid_native_evidence_does_not_claim_simulation_or_nc(self):
        result = self.inspect(1, [UUID], scene())
        self.assertEqual(result['verified_calculated_operations'], 1)
        self.assertFalse(result['simulation_verified'])
        mesh = result['operations'][0]['toolpath_meshes'][0]
        self.assertEqual(mesh['vertex_count'], 2)
        self.assertFalse(mesh['nc_export_verified'])

    def test_corrupt_and_nonfinite_buffers_fail_closed(self):
        for binary in (scene()[:-4], scene((float('nan'), 0, 0)), b'other_format'):
            with self.subTest(binary_size=len(binary)):
                with self.assertRaises(ValueError):
                    scene_vertices(binary, UUID, 'gcode_polyline')

    def test_counter_without_paths_is_not_evidence(self):
        result = self.inspect(1, [], scene())
        self.assertEqual(result['verified_calculated_operations'], 0)
