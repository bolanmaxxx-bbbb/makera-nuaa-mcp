"""Pure SVG placement guards and saved-result checks; no application or machine."""
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

UID='12345678-1234-1234-1234-123456789abc'


def fixture(path, center=(20,15), scale=1):
    matrix=[scale,0,0,0,0,1,0,0,0,0,1,0,*center,0,1]
    data=struct.pack('<ffffff',-2,-1,0,2,1,0)
    scene=(struct.pack('<I',8)+b'scene_v3'+struct.pack('<I',8)+b'SvgShape'
           +struct.pack('<I',36)+UID.encode()+struct.pack('<16f',*matrix)
           +b'\0'*34+struct.pack('<I',len(data))+data)
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('makera.bin',scene)
        z.writestr('makera.prj',json.dumps({'project':{'strProjectName':'synthetic',
            'stStockInfo':{'length':60,'width':40,'height':5,'StockShape':1},
            'lstCoordinate':{'0':{'images':[{'id':0,'type':1}], 'path':[]}}}}))


class SvgPlacementTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'synthetic.mks';fixture(self.path)
        self.before=inspect_project(str(self.path))
        self.studio=NativeStudio(Settings(Path(tmp.name)));self.studio.managed_project=self.path
        self.studio.read=lambda:{'available':True,'window_title':'MakeraStudio-[synthetic]'}
        self.studio._call=Mock(side_effect=AssertionError('Unexpected native call'))

    def place(self, **args):
        return self.studio.place_svg_layout(**{'center_mm':{'x':30,'y':10},
                                             'project_sha256':self.before['sha256'],**args})

    def test_invalid_targets_and_old_hash_never_write(self):
        for args in [{'center_mm':{'x':1}}, {'center_mm':{'x':True,'y':10}},
                     {'center_mm':{'x':float('nan'),'y':10}},
                     {'center_mm':{'x':30,'y':10,'z':0}},
                     {'center_mm':{'x':1,'y':10}}, {'project_sha256':'0'*64}]:
            with self.subTest(args=args),self.assertRaises(ValueError):self.place(**args)
        self.studio._call.assert_not_called()

    def test_scaled_svg_and_pending_cam_are_rejected(self):
        fixture(self.path,scale=2);self.before=inspect_project(str(self.path))
        with self.assertRaises(ValueError):self.place()
        fixture(self.path);self.before=inspect_project(str(self.path))
        self.studio.pending_tools[0]={'name':'pending'}
        with self.assertRaises(RuntimeError):self.place()
        self.studio._call.assert_not_called()

    def after(self):
        fixture(self.path,center=(30,10));result=inspect_project(str(self.path))
        fixture(self.path);return result

    def simulate(self, after):
        self.studio._call=Mock(side_effect=lambda method,*args:
            {'path_summary':{'modal_type':None},'place_svg_layout':{'invoked':True}}[method])
        self.studio.save_project=Mock(side_effect=[{'saved':True,'project':self.before},
                                                  {'saved':True,'project':after}])

    def test_native_success_without_saved_translation_is_failure(self):
        self.simulate(self.before);self.assertFalse(self.place()['verified'])

    def test_saved_translation_preserves_z_and_other_inputs(self):
        self.simulate(self.after());result=self.place()
        self.assertTrue(result['verified']);self.assertTrue(result['all_svg_shapes_translated'])
        self.assertEqual(result['delta_mm'],{'x':10,'y':-5})
        self.assertTrue(result['recalculation_required']);self.assertFalse(result['machine_commands_sent'])

    def test_changed_model_or_depth_invalidates_result(self):
        after=self.after();after['models_3d']=[{'unexpected':'change'}]
        self.simulate(after);self.assertFalse(self.place()['verified'])

    def test_changed_svg_local_geometry_invalidates_result(self):
        after=copy.deepcopy(self.after());after['svg_geometry'][0]['vertex_count']+=1
        self.simulate(after);self.assertFalse(self.place()['verified'])

    def test_unsaved_inputs_cannot_be_silently_consumed(self):
        self.studio._call=Mock(return_value={'modal_type':None})
        changed=copy.deepcopy(self.before);changed['stock']['height']=6
        self.studio.save_project=Mock(return_value={'saved':True,'project':changed})
        with self.assertRaises(RuntimeError):self.place()
        self.assertEqual(self.studio._call.call_count,1)


if __name__=='__main__':unittest.main()
