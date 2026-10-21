"""Synthetic input guards and native-save evidence, without Studio/device access."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import zipfile

from makera_mcp.config import Settings
from makera_mcp.native import NativeStudio
from makera_mcp.operation_catalog import OPERATIONS, operation_catalog
from makera_mcp.project import inspect_project
import test_model_placement


class ReliefNativeTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name);self.path=self.root/'synthetic.mks'
        test_model_placement.fixture(self.path,center=(20,15,-3))
        self.rewrite(lambda p:p['stStockInfo'].update(height=6))
        self.snapshot=inspect_project(str(self.path))
        self.studio=NativeStudio(Settings(self.root));self.studio.managed_project=self.path
        self.studio.read=lambda:{'available':True,'window_title':'MakeraStudio-[synthetic]'}
        self.studio._call=Mock(side_effect=AssertionError('Unexpected native call'))

    def rewrite(self,edit):
        with zipfile.ZipFile(self.path) as z:
            scene=z.read('makera.bin');project=json.loads(z.read('makera.prj'))['project']
        edit(project)
        with zipfile.ZipFile(self.path,'w') as z:
            z.writestr('makera.bin',scene);z.writestr('makera.prj',json.dumps({'project':project}))

    def configured(self,**overrides):
        op={'id':'synthetic-relief','type':6,'selectedmeshes':[test_model_placement.UUID],
            'finishing':[False],'machining_allowance':[.2],'tools':[]}
        op.update(overrides)
        self.rewrite(lambda p:p.update(lstCoordinate={'0':{'path':[op]}}))
        return inspect_project(str(self.path))

    def test_bad_parameters_stale_hash_and_pending_edits_never_write(self):
        uid=test_model_placement.UUID;digest=self.snapshot['sha256']
        for layered,allowance,sha in ((1,0,digest),(True,-1,digest),(True,True,digest),
                                      (True,float('nan'),digest),(True,2.1,digest),(True,0,'0'*64)):
            with self.subTest(layered=layered,allowance=allowance), self.assertRaises(ValueError):
                self.studio.create_relief(uid,layered,allowance,sha)
        self.studio.pending_tools[0]={}
        with self.assertRaises(RuntimeError):self.studio.create_relief(uid,True,0,digest)
        self.studio._call.assert_not_called()

    def test_unknown_mesh_stock_shape_and_outside_pose_rejected(self):
        with self.assertRaises(ValueError):self.studio.create_relief('0'*36,True,0,self.snapshot['sha256'])
        for field,value in (('StockShape',0),('height',20),('height',1)):
            self.rewrite(lambda p:p['stStockInfo'].update({field:value}))
            snapshot=inspect_project(str(self.path))
            with self.assertRaises(ValueError):self.studio.create_relief(test_model_placement.UUID,True,0,snapshot['sha256'])
        self.studio._call.assert_not_called()

    def test_configuration_does_not_accept_other_operation_types(self):
        snap=self.configured(type=7)
        with self.assertRaises(ValueError):self.studio.configure_relief(0,False,0,snap['sha256'])
        self.studio._call.assert_not_called()

    def test_layering_and_allowance_are_saved_evidence_not_editor_ack(self):
        snapshot=self.configured(finishing=[True],machining_allowance=[0])
        self.studio._call=Mock(side_effect=lambda name,*_: {'visible':True} if name=='show_path_list' else {'invoked':[True]})
        self.studio.save_project=Mock(return_value={'saved':True,'project':snapshot})
        result=self.studio._commit_relief(0,test_model_placement.UUID,True,.2)
        self.assertFalse(result['saved_verified'])
        result=self.studio._commit_relief(0,test_model_placement.UUID,False,0)
        self.assertTrue(result['saved_verified'])
        self.assertFalse(result['calculated']);self.assertFalse(result['machine_commands_sent'])

    def test_missing_explicit_tool_and_missing_model_block_generation(self):
        self.configured()
        with self.assertRaises(ValueError):self.studio.generate_toolpath(0)
        self.configured(selectedmeshes=['0'*36])
        with self.assertRaises(ValueError):self.studio.generate_toolpath(0)
        self.studio._call.assert_not_called()

    def test_catalog_marks_unimplemented_types_and_new_labels(self):
        labels=[row[2] for row in OPERATIONS]
        catalog=operation_catalog(labels+['Unknown future strategy'])
        self.assertEqual(len(catalog['operations']),14)
        self.assertEqual(len({row['native_type'] for row in catalog['operations']}),14)
        self.assertEqual(catalog['unknown_native_labels'],['Unknown future strategy'])
        for row in catalog['operations']:
            if row['id'] in ('feature_3d','rotary_relief','laser_image','thread_2d'):
                self.assertIsNone(row['mcp_creation_tool'])
                self.assertFalse(row['mcp_calculation_supported'])
        self.assertFalse(catalog['catalog_is_execution_permission'])

    def test_new_motion_does_not_hide_changed_relief_inputs(self):
        tool={'toolType':1,'diameter':2,'fluteLength':10,'spindleSpeed':10000,'feedrate':100,
              'plungeFeedrate':30,'stepDown':.5,'stepOverMM':.2}
        baseline=self.configured(tools=[tool])
        saved=copy.deepcopy(baseline)
        op=saved['operations'][0]
        op.update(calculation_evidence_verified=True,toolpath_meshes=[{'uuid':'new-synthetic-motion'}])
        op['relief_settings']['machining_allowance']=[1]
        self.studio._call=Mock(side_effect=lambda name,*_: {'modal_type':None,'paths':[{}]} if name=='path_summary' else {'queued':True})
        self.studio.save_project=Mock(return_value={'saved':True,'project':saved})
        result=self.studio.generate_toolpath(0)
        self.assertFalse(result['verified'])
        self.assertEqual(result['outcome'],'relief_inputs_changed_during_generation')

    def test_catalog_cleanup_on_read_failure_does_not_add_operations(self):
        calls=[]
        def native(name,*args):
            calls.append(name)
            if name=='path_type_options':raise RuntimeError('read failed')
            if name=='path_summary':return {'modal_type':'AddKnifeTypeDlg'}
            return {'queued':True}
        self.studio._call=native;self.studio._wait_modal=Mock()
        with self.assertRaises(RuntimeError):self.studio.operation_catalog()
        self.assertIn('cancel_path_chooser',calls)
        self.assertNotIn('choose_path_type',calls)
        self.assertEqual(inspect_project(str(self.path))['sha256'],self.snapshot['sha256'])


if __name__=='__main__':unittest.main()
