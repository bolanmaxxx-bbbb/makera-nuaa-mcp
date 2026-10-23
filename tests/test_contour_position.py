import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from makera_mcp.native import NativeStudio
from makera_mcp.config import Settings

class ContourPositionTests(unittest.TestCase):
 def setUp(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
  self.studio=NativeStudio(Settings(self.root));self.studio.managed_project=self.root/'synthetic.mks'
  self.studio.read=lambda:{'available':True,'window_title':'MakeraStudio-[synthetic]'}
  self.before={'stock':{},'svg_geometry':[],'models_3d':[],'operations':[{'id':'a','type':1,'contour_position':0,'geometry_uuids':['line'],'depth':{'endDepth':3.7},'tools':[{'diameter':3.175}],'tab_settings':{}}]}
 def test_invalid_mode_or_index_and_pending_edits_do_not_mutate(self):
  self.studio._call=Mock(side_effect=AssertionError('Unexpected native write'))
  with self.assertRaises(ValueError):self.studio.set_contour_position(0,'bad')
  with patch('makera_mcp.native.inspect_project',return_value=self.before):
   for index in (True,-1,1):
    with self.assertRaises(ValueError):self.studio.set_contour_position(index,'on_line')
  self.studio.pending_tools[0]={}
  with self.assertRaises(RuntimeError):self.studio.set_contour_position(0,'on_line')
  self.studio._call.assert_not_called()
 def run_saved(self,change_depth=False):
  after=copy.deepcopy(self.before);after['operations'][0]['contour_position']=2
  if change_depth:after['operations'][0]['depth']['endDepth']=4
  self.studio._call=Mock(side_effect=lambda method,*args:{'path_summary':{'modal_type':None,'paths':[{}]},'show_path_list':{'visible':True},'configure_contour_position':{'invoked':True,'expected_saved_index':2}}[method])
  self.studio.save_project=Mock(side_effect=[{'saved':True,'project':self.before},{'saved':True,'project':after}])
  with patch('makera_mcp.native.inspect_project',return_value=self.before):return self.studio.set_contour_position(0,'on_line')
 def test_position_saved_and_other_inputs_unchanged(self):
  self.assertTrue(self.run_saved()['verified'])
 def test_position_change_that_alters_depth_is_not_verified(self):
  self.assertFalse(self.run_saved(True)['verified'])
