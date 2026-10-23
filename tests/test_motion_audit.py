import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from makera_mcp.motion_audit import audit_segments, audit_saved_toolpath


class MotionAuditTests(unittest.TestCase):
    def test_same_layer_narrow_loop_flags_coverage(self):
        segments=[((0,0,-.1),(0,20,-.1)),((0,20,-.1),(.05,20,-.1)),
                  ((.05,20,-.1),(.05,0,-.1))]
        result=audit_segments(segments,2)
        self.assertEqual(result['same_layer_opposite_leg_pairs'],1)
        self.assertAlmostEqual(result['same_layer_examples'][0]['long_strip_overlap_fraction_estimate'],.975)
        self.assertTrue(result['efficiency_review_required'])
        self.assertFalse(result['actual_air_cut_length_verified'])

    def test_reciprocal_depth_change_is_distinguished(self):
        result=audit_segments([((0,0,-.1),(0,20,-.1)),((0,20,-.2),(0,0,-.2))],2)
        self.assertEqual(result['same_layer_opposite_leg_pairs'],0)
        self.assertEqual(result['opposite_leg_pairs_with_depth_change'],1)
        self.assertFalse(result['bidirectional_material_removal_verified'])

    def test_wide_spaced_tracks_and_invalid_data(self):
        result=audit_segments([((0,0,0),(0,20,0)),((5,20,0),(5,0,0))],2)
        self.assertEqual(result['same_layer_opposite_leg_pairs'],0)
        for diameter in (0,-1,True,float('nan')):
            with self.assertRaises(ValueError):audit_segments([],diameter)
        with self.assertRaises(ValueError):audit_segments([((0,0,0),(0,math.inf,0))],2)

    def test_saved_stale_motion_is_explicit_and_input_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'synthetic.mks';uid='12345678-1234-1234-1234-123456789abc'
            points=[(0,0,-.1),(0,20,-.1),(.05,20,-.1),(.05,0,-.1)]
            matrix=[1,0,0,0,0,1,0,0,0,0,1,0,4,3,0,1]
            data=b''.join(struct.pack('<fff',*v) for v in points)
            kind=b'gcode_polyline'
            scene=(struct.pack('<I',8)+b'scene_v3'+struct.pack('<I',len(kind))+kind
                   +struct.pack('<I',36)+uid.encode()+struct.pack('<16f',*matrix)+b'\0'*34
                   +struct.pack('<I',len(data))+data)
            project={'project':{'strProjectName':'synthetic','lstCoordinate':{'0':{'path':[
                {'id':'op','type':1,'emPathState':0,'gcodemeshuuidmap':{'motion':[uid]},
                 'tools':[{'diameter':2}],'depth':{'startDepth':0,'endDepth':.2}}]}}}}
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('makera.prj',json.dumps(project));z.writestr('makera.bin',scene)
            before=p.read_bytes();result=audit_saved_toolpath(str(p),0)
            self.assertEqual(result['same_layer_opposite_leg_pairs'],1)
            self.assertFalse(result['saved_calculation_evidence_verified'])
            self.assertEqual(before,p.read_bytes())
            self.assertFalse(result['nc_terminal_state_verified'])
            with self.assertRaises(ValueError):audit_saved_toolpath(str(p),True)
