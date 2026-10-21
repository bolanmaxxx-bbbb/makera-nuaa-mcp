"""Synthetic import/configuration guards; never attach to Studio or a device."""
import hashlib
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
import test_skill_mcp_workflow


class ProjectSetupTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root=Path(folder.name);self.path=self.root/'synthetic.mks'
        test_skill_mcp_workflow.write_project(self.path)
        self.studio=NativeStudio(Settings(self.root));self.studio.managed_project=self.path
        self.studio.read=lambda: {'available':True,'window_title':'MakeraStudio-[synthetic]'}
        self.studio._call=Mock(side_effect=AssertionError('Native call reached before validation'))

    def test_stock_shape_and_dimensions_are_validated_before_native_write(self):
        invalid=[{}, {'unknown':1}, {'shape':'sphere'}, {'shape':'box','dimensions_mm':{'x':2}},
                 {'shape':'cylinder','dimensions_mm':{'diameter':2,'z':True}},
                 {'shape':'box','dimensions_mm':{'x':2,'y':float('nan'),'z':4}},
                 {'dimensions_mm':{'x':2,'y':3,'z':4}},
                 {'material_category':'example'}, {'material_category':'','material_name':'example'},
                 {'dimensions_mm':{}}, {'stock_specification':'sample'},
                 {'stock_specification':'sample','material_category':'example','material_name':'example','shape':'box','dimensions_mm':{'x':2,'y':3,'z':4}}]
        for spec in invalid:
            with self.subTest(spec=spec),self.assertRaises(ValueError):self.studio.configure_stock(spec)
        self.studio._call.assert_not_called()

    def test_stock_ui_success_is_not_saved_field_verification(self):
        baseline=inspect_project(str(self.path))
        self.studio.save_project=Mock(return_value={'saved':True,'project':baseline})
        self.studio._call=Mock(side_effect=lambda name,*args: {
            'path_summary':{'modal_type':None},'stock_options':{'editor_visible':True},
            'configure_stock_editor':{'verified':True},'close_stock_editor':{'queued':True}}[name])
        result=self.studio.configure_stock({'shape':'box','dimensions_mm':{'x':12,'y':13,'z':4}})
        self.assertFalse(result['verified']);self.assertFalse(result['machining_ready'])

    def test_catalog_failure_requires_saved_rollback_evidence(self):
        baseline=inspect_project(str(self.path))
        self.studio.save_project=Mock(return_value={'saved':True,'project':baseline})
        self.studio._call=Mock(side_effect=lambda name,*args: {
            'path_summary':{'modal_type':None},'stock_options':{'editor_visible':True},
            'configure_stock_editor':{'verified':False,'rolled_back':True},'close_stock_editor':{'queued':True}}[name])
        result=self.studio.configure_stock({'material_category':'example','material_name':'missing'})
        self.assertFalse(result['verified']);self.assertTrue(result['rollback_saved_verified'])

    def test_import_rejects_empty_wrong_format_units_and_duplicate_flag(self):
        svg=self.root/'source.svg';svg.write_text('<svg/>',encoding='utf-8')
        bad=self.root/'source.txt';bad.write_text('data',encoding='utf-8')
        empty=self.root/'empty.stl';empty.write_bytes(b'')
        for path,units,duplicate in [(svg,'inch',False),(svg,'mm','yes'),(bad,'mm',False),(empty,'mm',False)]:
            with self.subTest(path=path,units=units),self.assertRaises(ValueError):self.studio.import_model(str(path),units,duplicate)
        self.studio._call.assert_not_called()

    def test_confirmed_duplicate_geometry_is_not_imported_again(self):
        svg=self.root/'source.svg';svg.write_text('<svg/>',encoding='utf-8')
        baseline=inspect_project(str(self.path))
        self.studio.save_project=Mock(return_value={'saved':True,'project':baseline})
        self.path.with_suffix('.imports.json').write_text(json.dumps([{'sha256':hashlib.sha256(svg.read_bytes()).hexdigest(),'native_geometry_ids':[test_skill_mcp_workflow.GEOMETRY]}]),encoding='utf-8')
        result=self.studio.import_model(str(svg))
        self.assertTrue(result['imported']);self.assertEqual(result['outcome'],'already_present')
        self.assertFalse(result['native_mutation_performed']);self.studio._call.assert_not_called()

    def test_utf8_stl_node_dimensions_come_from_saved_vertices(self):
        uid=test_skill_mcp_workflow.GEOMETRY;kind='fixtures/测试块.stl'.encode('utf-8')
        vertices=struct.pack('<ffffff',-5,-4,-2,5,4,2)
        scene=struct.pack('<I',8)+b'scene_v3'+struct.pack('<I',len(kind))+kind+struct.pack('<I',36)+uid.encode()+b'\0'*98+struct.pack('<I',len(vertices))+vertices
        with zipfile.ZipFile(self.path,'w') as z:
            z.writestr('makera.bin',scene)
            z.writestr('makera.prj',json.dumps({'project':{'lst3DModels':[{'name':'测试块.stl','list_meshes_uuids':[uid]}]}}))
        model=inspect_project(str(self.path))['models_3d'][0]
        self.assertTrue(model['geometry_dimensions_verified'])
        self.assertEqual(model['meshes'][0]['dimensions_mm'],{'x':10,'y':8,'z':4})


if __name__=='__main__':unittest.main()
