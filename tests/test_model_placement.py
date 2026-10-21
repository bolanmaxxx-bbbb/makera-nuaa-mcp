"""Synthetic placement evidence and preflight guards; no native application needed."""
import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock
import zipfile

from makera_mcp.config import Settings
from makera_mcp.native import NativeStudio
from makera_mcp.project import inspect_project

UUID = '12345678-1234-1234-1234-123456789abc'


def fixture(path, center=(20, 15, -5), invalid_matrix=False, x_scale=1):
    matrix = [1,0,0,0,0,1,0,0,0,0,1,0,*center,1]
    matrix[0] = x_scale
    if invalid_matrix:
        matrix[0] = float('nan')
    kind = b'fixtures/block.stl'
    vertices = struct.pack('<ffffff', -2,-1,-1, 2,1,1)
    scene = (struct.pack('<I',8)+b'scene_v3'+struct.pack('<I',len(kind))+kind
             +struct.pack('<I',36)+UUID.encode()+struct.pack('<16f',*matrix)
             +b'\0'*34+struct.pack('<I',len(vertices))+vertices)
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('makera.bin',scene)
        archive.writestr('makera.prj',json.dumps({'project':{
            'stStockInfo':{'length':40,'width':30,'height':12,'StockShape':1},
            'lst3DModels':[{'id':2,'name':'block.stl','list_meshes_uuids':[UUID]}]}}))


class ModelPlacementTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name)/'synthetic.mks'
        fixture(self.path)
        self.snapshot = inspect_project(str(self.path))
        self.studio = NativeStudio(Settings(Path(folder.name)))
        self.studio.managed_project = self.path
        self.studio.read = lambda: {'available':True,'window_title':'MakeraStudio-[synthetic]'}
        self.studio._call = Mock(side_effect=AssertionError('Unexpected native write'))

    def place(self, **overrides):
        args = {'model_uuid':UUID,'center_mm':{'x':20,'y':15,'z':-6},
                'project_sha256':self.snapshot['sha256']}
        args.update(overrides)
        return self.studio.place_model(**args)

    def test_invalid_coordinates_and_rotation_never_reach_native(self):
        for overrides in [
            {'center_mm':{'x':20,'y':15}},
            {'center_mm':{'x':True,'y':15,'z':-6}},
            {'center_mm':{'x':20,'y':float('inf'),'z':-6}},
            {'world_rotation_deg':{'x':90,'y':90,'z':0}},
            {'world_rotation_deg':{'x':0,'y':45,'z':0}},
            {'world_rotation_deg':{'x':0,'y':270,'z':0}},
            {'model_uuid':'unknown'}, {'project_sha256':'old'},
        ]:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.place(**overrides)
        self.studio._call.assert_not_called()

    def test_stale_project_and_outside_stock_are_rejected(self):
        for overrides in [{'project_sha256':'0'*64}, {'center_mm':{'x':0,'y':15,'z':-6}},
                          {'center_mm':{'x':20,'y':15,'z':1}}]:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.place(**overrides)
        self.studio._call.assert_not_called()

    def test_pending_cam_settings_block_placement(self):
        self.studio.pending_tools[0] = {'toolName':'pending'}
        with self.assertRaises(RuntimeError):
            self.place()
        self.studio._call.assert_not_called()

    def test_preexisting_scaling_or_reflection_is_not_treated_as_supported(self):
        for scale in [2,-1]:
            fixture(self.path,x_scale=scale)
            self.snapshot=inspect_project(str(self.path))
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                self.place()
        self.studio._call.assert_not_called()

    def test_multiple_models_are_not_affected_by_global_rotation(self):
        with zipfile.ZipFile(self.path) as archive:
            scene=archive.read('makera.bin');data=json.loads(archive.read('makera.prj'))
        data['project']['lst3DModels'].append(copy.deepcopy(data['project']['lst3DModels'][0]))
        with zipfile.ZipFile(self.path,'w') as archive:
            archive.writestr('makera.bin',scene);archive.writestr('makera.prj',json.dumps(data))
        self.snapshot=inspect_project(str(self.path))
        with self.assertRaises(ValueError):
            self.place()
        self.studio._call.assert_not_called()

    def simulate_saved_result(self, after):
        self.studio._call=Mock(side_effect=lambda method,*args: {
            'path_summary':{'modal_type':None},'place_single_model':{'invoked':True}}[method])
        self.studio.save_project=Mock(side_effect=[{'saved':True,'project':self.snapshot},
                                                   {'saved':True,'project':after}])

    def test_editor_success_does_not_hide_unchanged_saved_matrix(self):
        self.simulate_saved_result(self.snapshot)
        result=self.place()
        self.assertFalse(result['verified'])
        self.assertFalse(result['machining_ready'])

    def test_verified_pose_still_rejects_unrelated_geometry_changes(self):
        fixture(self.path,center=(20,15,-6))
        after=inspect_project(str(self.path))
        fixture(self.path)
        self.snapshot=inspect_project(str(self.path))
        after['svg_geometry']=[{'uuid':'unexpected'}]
        self.simulate_saved_result(after)
        result=self.place()
        self.assertFalse(result['verified'])
        self.assertFalse(result['unrelated_inputs_unchanged'])

    def test_saved_center_and_unchanged_inputs_are_required(self):
        fixture(self.path,center=(20,15,-6))
        after=inspect_project(str(self.path))
        fixture(self.path)
        self.snapshot=inspect_project(str(self.path))
        self.simulate_saved_result(after)
        result=self.place()
        self.assertTrue(result['verified'])
        self.assertTrue(result['recalculation_required'])
        self.assertFalse(result['machine_commands_sent'])

    def test_nonfinite_matrix_is_not_geometry_evidence(self):
        fixture(self.path,invalid_matrix=True)
        model=inspect_project(str(self.path))['models_3d'][0]
        self.assertFalse(model['geometry_dimensions_verified'])
        self.assertTrue(model['mesh_read_errors'])


if __name__=='__main__':
    unittest.main()
