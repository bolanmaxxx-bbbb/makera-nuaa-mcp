"""Planar editing guards with synthetic projects; no Qt/Frida calls."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from makera_mcp.config import Settings
from makera_mcp.native import NativeStudio
import test_skill_mcp_workflow


class PlanarNativeGuards(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.path = self.root / "synthetic.mks"
        test_skill_mcp_workflow.write_project(self.path, created=True, kind=0)
        self.studio = NativeStudio(Settings(self.root))
        self.studio.managed_project = self.path
        self.studio.read = lambda: {"available": True, "window_title": "MakeraStudio-[synthetic]"}

    def test_pocket_cannot_be_created_without_managed_project(self):
        self.studio.managed_project = None
        self.studio._call = Mock(side_effect=AssertionError("Native mutation reached"))
        with self.assertRaises(RuntimeError):
            self.studio.create_pocket([test_skill_mcp_workflow.GEOMETRY], 0.1)
        self.studio._call.assert_not_called()

    def test_invalid_depth_intervals_do_not_reach_native_rpc(self):
        self.studio._call = Mock(side_effect=AssertionError("Native mutation reached"))
        for start, end in ((-1, 1), (1, 1), (2, 1), (True, 1), (0, False), (float("nan"), 1), (0, float("inf")), (0, 11)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                self.studio.set_operation_depths(0, start, end)
        self.studio._call.assert_not_called()

    def test_unknown_indices_stock_overcut_and_3d_operations_are_rejected(self):
        self.studio._call = Mock(side_effect=AssertionError("Native mutation reached"))
        for index in (-1, True, 1, 0.5):
            with self.assertRaises(ValueError):
                self.studio.set_operation_depths(index, 0, 0.1)
        with self.assertRaises(ValueError):
            self.studio.set_operation_depths(0, 0, 6)
        test_skill_mcp_workflow.write_project(self.path, created=True, kind=7)
        with self.assertRaises(ValueError):
            self.studio.set_operation_depths(0, 0, 0.1)
        self.studio._call.assert_not_called()

    def test_modal_or_unsaved_operation_count_blocks_depth_mutation(self):
        for state in ({"modal_type": "UnknownDialog", "paths": [{}]}, {"modal_type": None, "paths": []}):
            with self.subTest(state=state):
                self.studio._call = Mock(return_value=state)
                with self.assertRaises(RuntimeError):
                    self.studio.set_operation_depths(0, 0, 0.1)
                self.assertEqual(self.studio._call.call_args.args, ("path_summary",))
                self.assertEqual(self.studio._call.call_count, 1)

    def test_overcut_requires_bounded_explicit_allowance(self):
        self.studio._call = Mock(side_effect=AssertionError('Native mutation reached'))
        for allowance in (-1, True, float('nan'), float('inf'), 2.1):
            with self.subTest(allowance=allowance), self.assertRaises(ValueError):
                self.studio.set_operation_depths(0, 0, 5.8, allowance)
        with self.assertRaises(ValueError):
            self.studio.set_operation_depths(0, 0, 5.8, 0.2)
        self.studio._call.assert_not_called()
        self.studio._call = Mock(return_value={'modal_type':'UnknownDialog','paths':[{}]})
        with self.assertRaises(RuntimeError):
            self.studio.set_operation_depths(0, 0, 5.8, 0.3)
        self.assertEqual(self.studio._call.call_args.args, ('path_summary',))

    def test_tab_geometry_and_operation_guards_precede_native_writes(self):
        self.studio._call = Mock(side_effect=AssertionError('Native mutation reached'))
        for count,width,height,shape in [(True,.3,.2,'rectangle'),(0,.3,.2,'rectangle'),(101,.3,.2,'rectangle'),(4,float('nan'),.2,'rectangle'),(4,.3,-1,'rectangle'),(4,.3,.2,'unknown')]:
            with self.subTest(count=count,width=width,height=height,shape=shape), self.assertRaises(ValueError):
                self.studio.configure_contour_tabs(0,count,width,height,shape)
        with self.assertRaises(ValueError):
            self.studio.configure_contour_tabs(0,4,.3,.2)
        test_skill_mcp_workflow.write_project(self.path,created=True,kind=1)
        with self.assertRaises(ValueError):
            self.studio.configure_contour_tabs(0,4,.3,.5)
        self.studio._call.assert_not_called()

    def test_detail_commit_cannot_hide_tool_parameter_changes(self):
        test_skill_mcp_workflow.write_project(self.path,created=True,kind=1)
        expected_tabs={'supportTabs':1,'tabsAutoNums':2,'tabsWidth':.3,'tabsHeight':.2,'tabsShape':1,'tabsLayout':1}
        self.studio.pending_tabs[0]={'native_operation_id':'synthetic-native','expected':expected_tabs}
        self.studio.pending_tools[0]={'stepOverMM':.2}
        self.studio._call=Mock(side_effect=lambda name,*args: {'modal_type':None,'paths':[{}]} if name=='path_summary' else {'queued':True})
        calculated={'id':'synthetic-native','calculation_evidence_verified':True,
            'toolpath_meshes':[{'uuid':'new-motion'}],'tools':[{'stepOverMM':0}], 'tab_settings':expected_tabs}
        self.studio.save_project=Mock(return_value={'saved':True,'project':{'operations':[calculated]}})
        result=self.studio.generate_toolpath(0)
        self.assertFalse(result['verified'])
        self.assertEqual(result['outcome'],'tabs_saved_but_tool_parameters_need_reselection')
        self.assertTrue(result['tab_settings_saved_verified'])
        self.assertFalse(result['machining_ready'])
        self.assertNotIn(0,self.studio.pending_tabs)
        self.assertIn(0,self.studio.pending_tools)

    def test_pending_tabs_for_another_native_operation_block_generation(self):
        test_skill_mcp_workflow.write_project(self.path,created=True,kind=1)
        self.studio.pending_tabs[0]={'native_operation_id':'different-native','expected':{}}
        self.studio._call=Mock(return_value={'modal_type':None,'paths':[{}]})
        with self.assertRaises(RuntimeError):
            self.studio.generate_toolpath(0)
        self.assertEqual(self.studio._call.call_args.args,('path_summary',))
        self.assertEqual(self.studio._call.call_count,1)


if __name__ == "__main__":
    unittest.main()
