"""A high, uncut path must not pass merely because it has no sampled undercuts."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile


ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('relief_regression',ROOT/'scripts/validate_3d_relief_mcp.py')
MODULE=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MODULE)
UUID='12345678-1234-1234-1234-123456789abc'


class SurfaceEvidenceTests(unittest.TestCase):
    def evidence(self,offset):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic.mks'
            points=[(15+i*.05,18,-6+MODULE.height('slope',3+i*.05,4)+offset) for i in range(128)]
            data=b''.join(struct.pack('<fff',*p) for p in points)
            kind=b'gcode_polyline'
            scene=(struct.pack('<I',8)+b'scene_v3'+struct.pack('<I',len(kind))+kind
                   +struct.pack('<I',36)+UUID.encode()+b'\0'*98+struct.pack('<I',len(data))+data)
            with zipfile.ZipFile(path,'w') as z:z.writestr('makera.bin',scene)
            return MODULE.surface_evidence(path,{'toolpath_meshes':[{'uuid':UUID}]},'slope')

    def test_surface_height_matches_within_flat_tool_gradient_bound(self):
        result=self.evidence(.05)
        self.assertEqual(result['sampled_vertex_undercuts'],0)
        self.assertEqual(result['surface_match_fraction'],1)
        self.assertGreater(result['interior_xyz_varying_segments'],100)

    def test_raised_path_is_not_accepted_as_surface_machining(self):
        result=self.evidence(1.4)
        self.assertEqual(result['sampled_vertex_undercuts'],0)
        self.assertEqual(result['surface_match_fraction'],0)

    def test_below_surface_is_reported_as_undercut(self):
        self.assertGreater(self.evidence(-.2)['sampled_vertex_undercuts'],100)


if __name__=='__main__':unittest.main()
