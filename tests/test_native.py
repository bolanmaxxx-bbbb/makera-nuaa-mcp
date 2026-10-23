import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from makera_mcp.config import Settings
from makera_mcp.native import NativeStudio


class NativeGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.studio = NativeStudio(Settings(self.root))

    def tearDown(self):
        self.temp.cleanup()

    def test_no_mutation_without_managed_project(self):
        with self.assertRaises(RuntimeError):
            self.studio.set_stock({"z": 5})
        with self.assertRaises(RuntimeError):
            self.studio.save_project()
        with self.assertRaises(RuntimeError):
            self.studio.create_contour(['unknown'], .1)
        with self.assertRaises(RuntimeError):
            self.studio.generate_toolpath(0)

    def test_changed_active_project_is_rejected(self):
        self.studio.managed_project = self.root / "managed.mks"
        self.studio.read = lambda: {"available": True, "window_title": "another project"}
        with self.assertRaises(RuntimeError):
            self.studio.set_stock({"z": 5})
        with self.assertRaises(RuntimeError):
            self.studio.save_project()

    def test_invalid_dimensions_fail_before_rpc(self):
        self.studio.managed_project = self.root / "managed.mks"
        self.studio.read = lambda: {"available": True, "window_title": "MakeraStudio-[managed]"}
        self.studio._call = lambda *args: self.fail("Invalid data reached native RPC")
        for values in ({}, {"a": 1}, {"x": True}, {"y": "2"}, {"z": -1}, {"x": float("nan")}):
            with self.assertRaises(ValueError):
                self.studio.set_stock(values)

    def test_project_name_prefix_does_not_authorize_other_project_writes(self):
        self.studio.managed_project=self.root/'managed.mks'
        self.studio.read=lambda:{'available':True,'window_title':'MakeraStudio-[managed2]'}
        self.studio._call=Mock(side_effect=AssertionError('Wrong project mutation'))
        for operation in (self.studio._require_managed,self.studio.save_project,lambda:self.studio.set_stock({'z':2})):
            with self.assertRaises(RuntimeError):operation()
        self.studio._call.assert_not_called()

    def test_original_file_cannot_be_opened_as_managed(self):
        source = self.root / "original.mks"
        source.write_bytes(b"not inspected because outside cases")
        with self.assertRaises(ValueError):
            self.studio.open_project(str(source))

    def test_dialog_blocks_switch_without_queuing_another_open(self):
        folder=self.root/'cases';folder.mkdir();source=folder/'synthetic.mks';source.touch()
        self.studio._call=Mock(return_value={'modal_type':'MyMessageBox'})
        with patch('makera_mcp.native.inspect_project',return_value={'native_path_entries':3}):
            result=self.studio.open_project(str(source))
        self.assertFalse(result['opened']);self.assertFalse(result['open_queued'])
        self.studio._call.assert_called_once_with('path_summary')


if __name__ == "__main__":
    unittest.main()
