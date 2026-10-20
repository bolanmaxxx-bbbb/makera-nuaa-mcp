import tempfile
import unittest
from pathlib import Path

from makera_mcp.config import Settings
from makera_mcp.tasks import TaskStore, validate_spec


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model = self.root / "part.svg"
        self.model.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        self.store = TaskStore(Settings(self.root))
        self.spec = {"model_path": str(self.model), "stock_mm": {"x": 20, "y": 20, "z": 3},
                     "material": "validation-only", "work_origin": "stock lower-left top",
                     "tools": [{"id": "T1", "type": "flat_endmill", "diameter_mm": 1.4, "shank_mm": 3.175}],
                     "operations": [{"type": "contour", "tool_id": "T1", "depth_mm": 1, "region": "outer profile",
                                     "rpm": 6000, "feed_mm_min": 100, "stepdown_mm": 0.1}],
                     "output_directory": str(self.root / "output")}

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_input_never_becomes_ready(self):
        task = self.store.create({"model_path": str(self.model)})
        self.assertEqual(task["status"], "needs_inputs")
        self.assertIn("stock_mm", self.store.next_action(task["id"])["missing_inputs"])

    def test_ready_task_is_not_completed(self):
        task = self.store.create(self.spec)
        self.assertEqual(self.store.next_action(task["id"])["stage"], "project_created")
        self.assertFalse(task["machine_execution_authorized"])
        self.assertEqual(task["stages"]["nc_exported"]["status"], "pending")
        self.assertEqual(TaskStore(Settings(self.root)).get(task["id"])["revision"], 1)

    def test_changed_model_is_detected(self):
        task = self.store.create(self.spec)
        self.model.write_text('changed')
        self.assertEqual(self.store.next_action(task["id"])["status"], "input_changed")

    def test_revise_invalidates_downstream(self):
        task = self.store.create(self.spec)
        updated = self.store.revise(task["id"], {**self.spec, "material": "revised material"})
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["stages"]["toolpaths_generated"]["status"], "invalidated")
        self.assertEqual(updated["stages"]["simulation_reviewed"]["status"], "invalidated")

    def test_invalid_values_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_spec({**self.spec, "stock_mm": {"x": float('nan'), "y": 20, "z": 3}})
        with self.assertRaises(ValueError):
            self.store.get('../outside')
        changed = {**self.spec, "operations": [{**self.spec["operations"][0], "tool_id": "T2"}]}
        with self.assertRaises(ValueError):
            validate_spec(changed)


if __name__ == "__main__":
    unittest.main()
