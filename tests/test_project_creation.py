import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from makera_mcp.config import Settings
from makera_mcp.native import NativeStudio

class ProjectCreationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);(self.root/'cases').mkdir()
        self.target=self.root/'cases'/'new.mks'
        self.studio=NativeStudio(Settings(self.root))
        self.studio._call=Mock(return_value={'modal_type':None,'paths':[]})
    def test_invalid_or_existing_destination_never_calls_native(self):
        self.target.touch()
        for path in (self.root/'new.mks',self.root/'cases'/'new.txt',self.target,self.root/'cases'/'missing'/'new.mks'):
            with self.subTest(path=path),self.assertRaises(ValueError):self.studio.create_project(str(path))
        self.studio._call.assert_not_called()
    def test_modal_blocks_new_without_queuing_action(self):
        self.studio._call.return_value={'modal_type':'MyMessageBox'}
        result=self.studio.create_project(str(self.target))
        self.assertFalse(result['created']);self.studio._call.assert_called_once_with('path_summary')
    def test_unmanaged_active_project_not_replaced(self):
        self.studio.read=Mock(return_value={'available':True,'window_title':'MakeraStudio-[user]'})
        with self.assertRaises(RuntimeError):self.studio.create_project(str(self.target))
        self.assertEqual([c.args[0] for c in self.studio._call.call_args_list],['path_summary'])
    def test_saved_empty_result_and_exact_title_required(self):
        old=self.root/'cases'/'old.mks';old.touch();self.studio.managed_project=old
        self.studio.read=Mock(side_effect=[{'available':True,'window_title':'MakeraStudio-[old]'},
            {'available':True,'window_title':'MakeraStudio-[old]'},
            {'available':True,'window_title':'MakeraStudio-[未定义]'},
            {'available':True,'window_title':'MakeraStudio-[new]'}])
        self.studio.save_project=Mock(return_value={'saved':True})
        self.studio._wait_modal=Mock()
        def native(method,*args):
            if method=='path_summary':return {'modal_type':None,'paths':[]}
            if method=='select_new_project_file':self.target.touch()
            return {'queued':True}
        self.studio._call.side_effect=native
        with patch('makera_mcp.native.inspect_project',return_value={'svg_geometry':[],'models_3d':[],'operations':[]}):
            result=self.studio.create_project(str(self.target))
        self.assertTrue(result['created']);self.assertFalse(result['machine_commands_sent'])
        self.assertEqual(self.studio.managed_project,self.target)
