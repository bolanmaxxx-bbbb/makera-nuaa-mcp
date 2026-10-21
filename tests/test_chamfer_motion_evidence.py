"""Reject incorrect side/depth in analytic chamfer evidence; no native Studio."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

SPEC = importlib.util.spec_from_file_location('chamfer_proof',Path(__file__).resolve().parents[1]/'scripts/validate_2d_chamfer_mcp.py')
PROOF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROOF)
UUID = '12345678-1234-1234-1234-123456789abc'


class ChamferMotionEvidenceTests(unittest.TestCase):
    def evidence(self,shape='rectangle',inside=False,z=-.5,wrong_side=False,start_depth=0):
        offset = -.25 if inside else .25
        if wrong_side: offset = -offset
        if shape=='circle':
            points = [(13+3+offset,23,z),(13-3-offset,23,z),(13,23+3+offset,z),(13,23-3-offset,z)]
            bounds = {'x':[0,6],'y':[0,6],'z':[0,0]}
        else:
            points = [(10-offset,25,z),(22+offset,25,z),(16,20-offset,z),(16,30+offset,z)]
            bounds = {'x':[0,12],'y':[0,10],'z':[0,0]}
        data = b''.join(struct.pack('<fff',*point) for point in points)
        scene = struct.pack('<I',8)+b'scene_v3'+struct.pack('<I',14)+b'gcode_polyline'+struct.pack('<I',36)+UUID.encode()+b'\0'*98+struct.pack('<I',len(data))+data
        geometry = {'node_matrix_column_major':[1,0,0,0,0,1,0,0,0,0,1,0,10,20,0,1],
            'bounds_in_serialized_coordinates_mm':bounds}
        op = {'toolpath_meshes':[{'uuid':UUID}],'depth':{'startDepth':start_depth,'endDepth':.3},
            'chamfer_settings':{'contourPosition':1 if inside else 0,'chamferTipOffset':.2},
            'tools':[{'tipDiameter':.1,'angle':90}]}
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)/'synthetic.mks'
            with zipfile.ZipFile(project,'w') as archive: archive.writestr('makera.bin',scene)
            return PROOF.chamfer_motion_evidence(project,op,geometry,shape)

    def test_correct_inside_outside_motion_matches_translated_edges(self):
        for shape in ('rectangle','circle'):
            for inside in (True,False):
                with self.subTest(shape=shape,inside=inside):
                    evidence = self.evidence(shape,inside)
                    self.assertEqual(evidence['floor_vertices_checked'],4)
                    self.assertEqual(evidence['edge_match_fraction'],1)
                    self.assertEqual(evidence['under_depth_vertices'],0)

    def test_motion_on_wrong_side_fails_even_with_correct_tip_depth(self):
        self.assertEqual(self.evidence(wrong_side=True)['edge_match_fraction'],0)

    def test_high_or_low_motion_has_no_valid_final_depth_samples(self):
        for z in (-.1,-.8):
            self.assertEqual(self.evidence(z=z)['floor_vertices_checked'],0)
        self.assertEqual(self.evidence(z=-.8)['under_depth_vertices'],4)

    def test_nonzero_start_moves_tip_below_absolute_end_assumption(self):
        self.assertEqual(self.evidence(start_depth=.1,z=-.6)['edge_match_fraction'],1)
        self.assertEqual(self.evidence(start_depth=.1,z=-.5)['floor_vertices_checked'],0)


if __name__=='__main__': unittest.main()
