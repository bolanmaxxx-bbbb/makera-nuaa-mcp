"""Geometry-only tool storage guards with synthetic native library responses."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from makera_mcp.config import Settings
from makera_mcp.native import NativeStudio
import test_skill_mcp_workflow as workflow


class InventoryNativeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.project=self.root/'synthetic.mks'
        workflow.write_project(self.project,created=True)
        self.studio=NativeStudio(Settings(self.root));self.studio.managed_project=self.project
        self.studio.read=lambda:{'available':True,'window_title':'MakeraStudio-[synthetic]'}
        self.spec={'kind':'endmill','name':'Synthetic inventory flat','diameter':2,'shank_diameter':3.175,'flute_length':6}
        self.studio._call=Mock(side_effect=AssertionError('Unexpected native call'))

    def register(self,**kwargs):
        args=dict(operation_index=0,group='Synthetic inventory',specification=self.spec)
        args.update(kwargs);return self.studio.register_inventory_tool(**args)

    def tables(self,existing=True,profiles=0,bad_diameter=False):
        return [{'name':'tableWidget_tool','values':[[self.spec['name']]] if existing else []},
            {'name':'tableWidget_material','rows':profiles,'values':[['unexpected']] if profiles else []},
            {'name':'tableWidget_tool_infomation','row_labels':['名称','类型','直径（DC）','刀柄直径(DS)','刃长(LC)'],
             'values':[[self.spec['name']],['平底刀'],['3' if bad_diameter else '2'],['3.175'],['6']]}]

    def native(self,new=False,profiles=0,bad_diameter=False,round_draft=False):
        saved=[not new]
        def call(name,*args):
            if name=='path_summary':return {'modal_type':None,'paths':[{}]}
            if name=='ensure_inventory_group':return {'verified':True,'created':False}
            if name=='tool_tables':return self.tables(saved[0],profiles,bad_diameter)
            if name=='select_library_tool':return {'material_selected':False}
            if name=='configure_tool_dialog':return {'invoked':[True],'save_enabled':True}
            if name=='read_tool_geometry_draft':return {**self.spec,'diameter':2.001} if round_draft else copy.deepcopy(self.spec)
            if name=='save_tool_dialog':saved[0]=True
            return {'queued':True}
        self.studio._call=Mock(side_effect=call);self.studio._wait_modal=Mock()

    def test_missing_unknown_or_nonfinite_dimensions_never_write(self):
        for changes in ({'kind':'V'},{'diameter':None},{'diameter':True},{'diameter':float('nan')},
                        {'flute_length':0},{'shank_diameter':101},{'name':''},{'name':' bad '},{'name':'bad\nname'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.register(specification={**self.spec,**changes})
        with self.assertRaises(ValueError):self.register(specification={**self.spec,'rpm':10000})
        with self.assertRaises(ValueError):self.register(specification={k:v for k,v in self.spec.items() if k!='flute_length'})
        self.studio._call.assert_not_called()

    def test_invalid_index_group_and_creation_flag_never_write(self):
        for changes in ({'operation_index':True},{'operation_index':-1},{'operation_index':1},
                        {'create_group_if_missing':1},{'group':''},{'group':' bad '},{'group':'bad\nname'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.register(**changes)
        self.studio._call.assert_not_called()

    def test_pending_cam_edits_prevent_inventory_registration(self):
        for field in ('pending_tools','pending_tabs','pending_chamfer'):
            getattr(self.studio,field)[0]={}
            with self.assertRaises(RuntimeError):self.register()
            getattr(self.studio,field).clear()
        self.studio._call.assert_not_called()

    def test_new_geometry_is_saved_without_materials_or_operation_selection(self):
        before=self.project.read_bytes();self.native(new=True)
        result=self.register()
        self.assertTrue(result['verified']);self.assertTrue(result['created']);self.assertTrue(result['saved_and_reopened'])
        self.assertFalse(result['cutting_parameters_set']);self.assertFalse(result['operation_tool_selected'])
        self.assertEqual(result['active_material_profile_count'],0)
        actions=[call.args[1] for call in self.studio._call.call_args_list if call.args[0]=='tool_library_action']
        self.assertNotIn('choose',actions);self.assertNotIn('edit_tool',actions)
        self.assertFalse(any('material' in call.args[0] for call in self.studio._call.call_args_list))
        self.assertEqual(before,self.project.read_bytes())

    def test_existing_matching_inventory_is_idempotent(self):
        self.native();result=self.register()
        self.assertTrue(result['verified']);self.assertFalse(result['created'])
        self.assertFalse(any(c.args==('tool_library_action','add_tool') for c in self.studio._call.call_args_list))
        self.studio._call.assert_any_call('ensure_inventory_group','Synthetic inventory',False)

    def test_group_creation_uses_owned_rename_prompt_then_reverifies_name(self):
        self.native(new=True);base=self.studio._call.side_effect;first=[True]
        def call(name,*args):
            if name=='ensure_inventory_group' and first[0]:
                first[0]=False;return {'verified':False,'created':True,'rename_queued':True}
            return base(name,*args)
        self.studio._call.side_effect=call
        self.assertTrue(self.register(create_group_if_missing=True)['verified'])
        self.studio._call.assert_any_call('accept_inventory_group_name','Synthetic inventory')
        self.studio._call.assert_any_call('ensure_inventory_group','Synthetic inventory',False)
        self.studio._wait_modal.assert_any_call('QInputDialog')

    def test_existing_geometry_or_profiles_cannot_be_silently_overwritten(self):
        for options in ({'profiles':1},{'bad_diameter':True}):
            self.native(**options)
            with self.subTest(options=options),self.assertRaises(RuntimeError):self.register()
            self.assertFalse(any(c.args[0] in ('configure_tool_dialog','save_tool_dialog') for c in self.studio._call.call_args_list))

    def test_rounded_geometry_draft_is_not_saved(self):
        self.native(new=True,round_draft=True)
        with self.assertRaises(RuntimeError):self.register()
        self.assertFalse(any(c.args[0]=='save_tool_dialog' for c in self.studio._call.call_args_list))


if __name__=='__main__':unittest.main()
