"""Synthetic offline contacts: no device connection or machining authorization."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_skill_job_gate


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/makera-studio-cam"
SCRIPT = SKILL / "scripts/check_thickness_measurement.py"
SPEC = importlib.util.spec_from_file_location("thickness_checker", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture():
    record = {
        "schema_version": 1,
        "context": {"id": "synthetic-context", "setup_id": "fixture-setup", "datum_id": "support",
                    "coordinate_epoch": "synthetic-epoch", "probe_id": "synthetic-probe",
                    "calibration_id": "synthetic-calibration", "frame": "machine", "units": "mm",
                    "z_positive_up": True, "report_format": "grbl_prb", "report_frame_verified": True,
                    "probe_calibration_current": True, "confirmed_current_setup": True},
        "points": {"backing_top": {"x": 10, "y": 12}, "stock_top": {"x": 30, "y": 32}},
        "xy_tolerance_mm": 0.001, "minimum_contacts_per_surface": 2, "max_repeat_spread_mm": 0.01,
        "backing_transfer": {"delta_z_mm": 0.1, "basis": "synthetic plane transfer"},
        "interlayer": {"thickness_mm": 0.2, "basis": "synthetic measured layer"},
        "fitness": {"estimated_error_bound_mm": 0.02, "allowed_error_mm": 0.03,
                    "basis": "synthetic error budget including transfer and probe"},
        "readings": [], "evidence": "synthetic retained feedback only"}
    for i, (surface, z) in enumerate((("backing_top", -20), ("backing_top", -19.996),
                                       ("stock_top", -15), ("stock_top", -14.996))):
        xy = record["points"][surface]
        record["readings"].append({"id": f"synthetic-{i}", "context_id": "synthetic-context",
            "surface": surface, "measured_at": f"2020-01-01T00:00:0{i}+00:00",
            "raw_report": f"[PRB:{xy['x']},{xy['y']},{z}:1]", "acquisition_ok": True,
            "controller_state_after": "Idle"})
    return record


class ThicknessMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.record = fixture()

    def check(self):
        return MODULE.validate_measurement(self.record)

    def test_compensation_sign_and_job_handoff(self):
        result = self.check()
        self.assertTrue(result["measurement_usable"], result)
        self.assertAlmostEqual(result["derived"]["thickness_mm"], 4.7)
        measurement = result["stock_measurement"]
        self.assertAlmostEqual(measurement["bottom_z_mm"], -19.898)
        gate = test_skill_job_gate.JobGateTests()
        gate.setUp()
        self.addCleanup(gate.doCleanups)
        gate.job["stock"]["measurement"] = measurement
        gate.job["operations"][0]["depth_mm"] = 0.7
        checked = test_skill_job_gate.validate_job(gate.job, "cam", gate.caps)
        self.assertTrue(checked["ready_for_requested_stage"], checked)
        self.assertAlmostEqual(checked["derived_metrics"]["actual_thickness_mm"], 4.7)

    def test_failed_or_alarm_contacts_never_yield_handoff(self):
        for raw in ("[PRB:10,12,-20:0]", "[PRB:10,12,-20:1]\nALARM:4",
                    "[PRB:10,12,-20:1]\nerror:9", "[PRB:10,12,-20:1][PRB:10,12,-21:1]",
                    "[PRB:10,12,nan:1]", "[PRB:10,12,-20:1][PRB:broken"):
            with self.subTest(raw=raw):
                self.record = fixture()
                self.record["readings"][0]["raw_report"] = raw
                self.assertFalse(self.check()["record_valid"])
                self.assertIsNone(self.check()["stock_measurement"])

    def test_context_idle_and_acquisition_must_match(self):
        for field, value in (("context_id", "old-context"), ("controller_state_after", "Run"),
                             ("acquisition_ok", False)):
            with self.subTest(field=field):
                self.record = fixture()
                self.record["readings"][0][field] = value
                self.assertFalse(self.check()["measurement_usable"])

    def test_wrong_point_and_unknown_report_frame_block(self):
        self.record["readings"][0]["raw_report"] = "[PRB:11,12,-20:1]"
        self.assertFalse(self.check()["record_valid"])
        self.record = fixture()
        self.record["context"]["report_frame_verified"] = False
        self.assertFalse(self.check()["record_valid"])

    def test_work_offset_revision_required(self):
        self.record["context"]["frame"] = "work"
        self.assertFalse(self.check()["record_valid"])
        self.record["context"]["work_offset_revision"] = "synthetic-offset"
        self.assertTrue(self.check()["measurement_usable"])

    def test_single_contact_is_only_provisional(self):
        self.record["readings"] = self.record["readings"][::2]
        result = self.check()
        self.assertTrue(result["record_valid"])
        self.assertAlmostEqual(result["derived"]["thickness_mm"], 4.7)
        self.assertFalse(result["measurement_usable"])
        self.assertIsNone(result["stock_measurement"])

    def test_repeat_spread_and_error_budget_are_independent(self):
        for group, field, value in ((None, "max_repeat_spread_mm", 0.001),
                                    ("fitness", "estimated_error_bound_mm", 0.04)):
            self.record = fixture()
            (self.record[group] if group else self.record)[field] = value
            self.assertFalse(self.check()["measurement_usable"])
            self.assertTrue(self.check()["record_valid"])

    def test_unknown_transfer_layer_and_zero_without_basis_block(self):
        for group, field, value in (("backing_transfer", "delta_z_mm", None),
                                   ("interlayer", "thickness_mm", None),
                                   ("backing_transfer", "basis", None), ("interlayer", "basis", None)):
            self.record = fixture()
            self.record[group][field] = value
            self.assertFalse(self.check()["measurement_usable"])
            self.assertIsNone(self.check()["stock_measurement"])

    def test_explicit_zero_with_basis_is_valid(self):
        self.record["backing_transfer"]["delta_z_mm"] = 0
        self.record["interlayer"]["thickness_mm"] = 0
        self.assertAlmostEqual(self.check()["derived"]["thickness_mm"], 5)
        self.assertTrue(self.check()["measurement_usable"])

    def test_current_setup_and_calibration_cannot_be_inferred(self):
        for field in ("confirmed_current_setup", "probe_calibration_current"):
            self.record = fixture()
            self.record["context"][field] = False
            self.assertFalse(self.check()["measurement_usable"])
            self.assertTrue(self.check()["record_valid"])

    def test_negative_thickness_rejected(self):
        self.record["interlayer"]["thickness_mm"] = 10
        self.assertFalse(self.check()["record_valid"])

    def test_duplicate_acquisition_and_naive_time_rejected(self):
        for field, value in (("measured_at", "2020-01-01T00:00:00+00:00"),
                             ("measured_at", "2020-01-01T00:00:01"), ("id", "synthetic-0")):
            self.record = fixture()
            self.record["readings"][1][field] = value
            self.assertFalse(self.check()["record_valid"])

    def test_structured_adapter_requires_trigger_and_raw_evidence(self):
        self.record["context"]["report_format"] = "latched_xyz"
        for sample in self.record["readings"]:
            sample["latched_xyz_mm"] = MODULE.latched_xyz(sample, "grbl_prb")
            sample["trigger_success"] = True
        self.assertTrue(self.check()["measurement_usable"])
        self.record["readings"][0]["trigger_success"] = False
        self.assertFalse(self.check()["record_valid"])

    def test_boolean_and_overflow_coordinates_rejected(self):
        self.record["points"]["backing_top"]["x"] = True
        self.assertFalse(self.check()["record_valid"])
        self.record = fixture()
        self.record["readings"][0]["raw_report"] = "[PRB:10,12,-1e308:1]"
        self.record["readings"][1]["raw_report"] = "[PRB:10,12,1e308:1]"
        result = self.check()
        self.assertFalse(result["record_valid"])
        json.dumps(result, allow_nan=False)

    def test_four_axis_feedback_requires_unchanged_rotary_pose(self):
        for sample in self.record["readings"]:
            sample["raw_report"] = sample["raw_report"].replace(":1]", ",-90:1]")
        self.assertTrue(self.check()["measurement_usable"])
        self.record["readings"][-1]["raw_report"] = self.record["readings"][-1]["raw_report"].replace(",-90:", ",0:")
        self.assertFalse(self.check()["record_valid"])

    def test_check_is_read_only_and_ignores_approach_as_authorization(self):
        self.record["approach"] = {"requested_clearance_mm": 20, "execution_mode": "unverified"}
        before = copy.deepcopy(self.record)
        result = self.check()
        self.assertEqual(self.record, before)
        self.assertTrue(result["measurement_usable"])
        self.assertFalse(result["machine_commands_sent"])
        self.assertNotIn("commands", result)

    def test_cli_success_incomplete_and_malformed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "contacts.json"
            for data, code in ((self.record, 0),
                               (json.loads((SKILL / "assets/thickness-measurement-template.json").read_text(encoding="utf-8")), 2),
                               ({"context": []}, 3)):
                path.write_text(json.dumps(data), encoding="utf-8")
                before = path.read_bytes()
                run = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
                self.assertEqual(run.returncode, code, run.stdout + run.stderr)
                self.assertFalse(json.loads(run.stdout)["machine_commands_sent"])
                self.assertEqual(before, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
