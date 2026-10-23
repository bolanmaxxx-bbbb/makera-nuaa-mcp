import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from makera_mcp.native import NativeStudio
from makera_mcp.config import Settings


class OperationGeometryTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.studio=NativeStudio(Settings(Path(t.name)));self.studio.managed_project=Path(t.name)/'synthetic.mks'
        self.studio.read=lambda:{'available':True,'window_title':'MakeraStudio-[synthetic]'}
        self.before={'stock':{},'svg_geometry':[{'uuid':'old'},{'uuid':'new'}],'models_3d':[],
            'operations':[{'id':'operation','type':1,'depth':{'startDepth':.2,'endDepth':.8},
                           'tools':[{'diameter':2}],'geometry_uuids':['old'],'tab_settings':{},'contour_position':2}]}
        self.studio._call=Mock(side_effect=AssertionError('Unexpected native mutation'))
    def test_invalid_selection_and_pending_edits_do_not_mutate(self):
        with patch('makera_mcp.native.inspect_project',return_value=self.before):
            for ids in ([],['unknown'],['new','new'],[None]):
                with self.subTest(ids=ids),self.assertRaises(ValueError):self.studio.set_operation_geometry(0,ids)
            for index in (True,-1,1):
                with self.assertRaises(ValueError):self.studio.set_operation_geometry(index,['new'])
        self.studio.pending_tools[0]={}
        with self.assertRaises(RuntimeError):self.studio.set_operation_geometry(0,['new'])
        self.studio._call.assert_not_called()
    def simulate(self,alter_depth=False):
        after=copy.deepcopy(self.before);after['operations'][0]['geometry_uuids']=['new']
        if alter_depth:after['operations'][0]['depth']['startDepth']=0
        self.studio._call=Mock(side_effect=lambda method,*args:{'path_summary':{'modal_type':None,'paths':[{}]},
             'show_path_list':{'visible':True},'configure_contour':{'invoked':[True,True,True]}}[method])
        self.studio.save_project=Mock(side_effect=[{'saved':True,'project':self.before},{'saved':True,'project':after}])
        with patch('makera_mcp.native.inspect_project',return_value=self.before):return self.studio.set_operation_geometry(0,['new'])
    def test_rebinding_preserves_nonzero_start_depth_and_tool(self):
        result=self.simulate();self.assertTrue(result['verified']);self.assertTrue(result['recalculation_required'])
    def test_unintended_start_depth_reset_is_not_verified(self):
        self.assertFalse(self.simulate(True)['verified'])
