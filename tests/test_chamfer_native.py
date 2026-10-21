"""Chamfer geometry, revision and commit guards; synthetic projects, no machine."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import zipfile

from makera_mcp.config import Settings
from makera_mcp.native import NativeStudio
from makera_mcp.project import inspect_project
import test_skill_mcp_workflow as workflow_tests


class ChamferNativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'synthetic.mks'
        workflow_tests.write_project(self.path, created=True, kind=13)
        self.tool = {'toolName':'Synthetic V','toolType':2,'diameter':3.175,'handleDiameter':3.175,'tipDiameter':.1,'angle':90,
            'fluteLength':1.6,'spindleSpeed':10000,'feedrate':100,'plungeFeedrate':40,'stepDown':.1,'stepOverMM':.05}
        self.rewrite(lambda op: op.update(tools=[self.tool],chamferTipOffset=.2,contourPosition=0,workDirection=1))
        self.studio = NativeStudio(Settings(self.root))
        self.studio.managed_project = self.path
        self.studio.read = lambda: {'available':True,'window_title':'MakeraStudio-[synthetic]'}
        self.studio._call = Mock(side_effect=AssertionError('Unexpected native call'))

    def rewrite(self, update):
        with zipfile.ZipFile(self.path) as z:
            data, scene = json.loads(z.read('makera.prj')), z.read('makera.bin')
        update(data['project']['lstCoordinate']['0']['path'][0])
        with zipfile.ZipFile(self.path,'w') as z:
            z.writestr('makera.prj',json.dumps(data)); z.writestr('makera.bin',scene)

    def configure(self, **overrides):
        args = dict(operation_index=0,start_depth_mm=0,chamfer_depth_mm=.5,tip_offset_mm=.2,
            side='outside',direction='climb',project_sha256=inspect_project(str(self.path))['sha256'])
        args.update(overrides)
        return self.studio.configure_chamfer(**args)

    def test_invalid_inputs_and_stale_revision_never_open_details(self):
        for overrides in ({'operation_index':True},{'operation_index':1},{'start_depth_mm':-.1},
                          {'chamfer_depth_mm':float('nan')},{'tip_offset_mm':True},{'tip_offset_mm':2.1},
                          {'side':'on'},{'direction':'automatic'},{'project_sha256':'0'*64}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.configure(**overrides)
        self.studio._call.assert_not_called()

    def test_generic_absolute_depth_setter_does_not_accept_chamfer_size(self):
        with self.assertRaises(ValueError): self.studio.set_operation_depths(0,0,.3)
        self.studio._call.assert_not_called()

    def test_tip_offset_counts_toward_cone_and_stock_limits(self):
        # 90 degree V with DC3.175 and tip0.1 has 1.5375 mm of usable cone.
        with self.assertRaises(ValueError): self.configure(chamfer_depth_mm=1.2,tip_offset_mm=.4)
        with self.assertRaises(ValueError): self.configure(start_depth_mm=5.2,chamfer_depth_mm=5.4,tip_offset_mm=.2)
        self.studio._call.assert_not_called()

    def test_nonzero_start_is_added_to_chamfer_size_not_compared_as_end(self):
        snapshot = inspect_project(str(self.path))
        self.studio._chamfer_preflight(snapshot,0,1.2,.2,.2,'outside','climb')
        with self.assertRaises(ValueError):
            self.studio._chamfer_preflight(snapshot,0,1.21,.2,.2,'outside','climb')
        self.studio._call.assert_not_called()

    def test_wrong_type_or_missing_geometry_is_not_a_chamfer(self):
        for change in ({'type':1},{'selectedmeshes':['0'*36]},{'selectedmeshes':[]}):
            self.rewrite(lambda op: op.update(change))
            with self.assertRaises(ValueError): self.configure()
        self.studio._call.assert_not_called()

    def test_wrong_tool_angle_tip_or_zero_rate_never_calculates(self):
        for change in ({'toolType':1},{'angle':180},{'angle':0},{'angle':60},{'tipDiameter':3.175},
                       {'tipDiameter':-1},{'feedrate':0},{'stepDown':2}):
            self.rewrite(lambda op: op.update(tools=[{**self.tool,**change}]))
            with self.subTest(change=change), self.assertRaises(ValueError): self.studio.generate_toolpath(0)
        self.studio._call.assert_not_called()

    def test_pending_edits_or_modal_prevent_chamfer_mutation(self):
        self.studio.pending_tabs[1] = {}
        with self.assertRaises(RuntimeError): self.configure()
        self.studio._call.assert_not_called()
        self.studio.pending_tabs.clear()
        self.studio._call = Mock(return_value={'modal_type':'Unknown','paths':[{}]})
        with self.assertRaises(RuntimeError): self.configure()
        self.assertEqual(self.studio._call.call_args_list[0].args,('path_summary',))
        self.assertEqual(self.studio._call.call_count,1)

    def stage(self):
        def native(name,*args):
            if name=='path_summary': return {'modal_type':None,'paths':[{}]}
            if name=='open_operation_details': return {'queued':True}
            if name=='configure_chamfer_details': return {'invoked':[True],'choices_checked':True,
                'readback':{'lineEdit_start_depth_2':0,'lineEdit_end_depth_2':.5,'lineEdit_finish_2':.2}}
            return {'queued':True}
        self.studio._call = Mock(side_effect=native)
        return self.configure()

    def test_editor_acknowledgement_is_not_saved_calculation(self):
        before = self.path.read_bytes()
        result = self.stage()
        self.assertTrue(result['verified'])
        self.assertFalse(result['saved_verified'])
        self.assertFalse(result['machining_ready'])
        self.assertEqual(before,self.path.read_bytes())

    def test_save_changed_since_staging_blocks_generation(self):
        self.stage()
        self.rewrite(lambda op:op.update(chamferTipOffset=.3))
        self.studio._call = Mock(side_effect=AssertionError('No native call allowed'))
        with self.assertRaises(RuntimeError): self.studio.generate_toolpath(0)
        self.studio._call.assert_not_called()

    def test_new_motion_cannot_hide_wrong_side_or_tip_offset(self):
        self.stage()
        calculated = copy.deepcopy(inspect_project(str(self.path))['operations'][0])
        calculated.update(calculation_evidence_verified=True,toolpath_meshes=[{'uuid':'new-motion'}])
        calculated['chamfer_settings']['contourPosition'] = 1
        self.studio.save_project = Mock(return_value={'saved':True,'project':{'operations':[calculated]}})
        result = self.studio.generate_toolpath(0)
        self.assertFalse(result['verified'])
        self.assertEqual(result['outcome'],'chamfer_inputs_changed_during_generation')
        self.assertFalse(result['machine_commands_sent'])

    def test_revision_preserves_expected_tool_even_without_prior_selection(self):
        self.stage()
        calculated = copy.deepcopy(inspect_project(str(self.path))['operations'][0])
        calculated.update(calculation_evidence_verified=True,toolpath_meshes=[{'uuid':'new-motion'}])
        calculated['tools'][0]['stepOverMM'] = 0
        self.studio.save_project = Mock(return_value={'saved':True,'project':{'operations':[calculated]}})
        result = self.studio.generate_toolpath(0)
        self.assertFalse(result['verified'])
        self.assertEqual(result['outcome'],'chamfer_saved_but_tool_parameters_need_reselection')
        self.assertTrue(result['chamfer_settings_saved_verified'])
        self.assertNotIn(0,self.studio.pending_chamfer)
        self.assertIn(0,self.studio.pending_tools)

    def test_wrong_cutter_selection_closes_chooser_and_keeps_pending_empty(self):
        self.studio._wait_modal = Mock()
        def native(name,*args):
            if name=='select_library_tool': return {'material_selected':True}
            if name=='tool_tables': return [{'name':'tableWidget_tool_infomation',
                'row_labels':['类型'],'values':[['平底刀']]}]
            if name=='path_summary': return {'modal_type':'CutterLibWgt'}
            return {'queued':True}
        self.studio._call = Mock(side_effect=native)
        with self.assertRaises(ValueError): self.studio.select_tool(0,'Synthetic','Flat','Synthetic')
        self.studio._call.assert_any_call('tool_library_action','close')
        self.assertFalse(self.studio.pending_tools)


if __name__ == '__main__':
    unittest.main()
