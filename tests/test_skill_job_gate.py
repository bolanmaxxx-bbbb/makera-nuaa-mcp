"""Offline regression tests. Synthetic capabilities are NOT Studio verification."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "makera-studio-cam"
SCRIPT = SKILL / "scripts" / "check_job.py"
SPEC = importlib.util.spec_from_file_location("skill_job_gate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
validate_job = MODULE.validate_job


class JobGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        model = Path(self.temp.name) / "fixture.svg"
        model.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        self.job = {
            "schema_version": 1, "revision": 1, "scope": "production_cam",
            "scope_source": "user_confirmed", "process": "rotary", "units": "mm",
            "job_kind": "plate_part", "setup_id": "fixture-setup",
            "model": {"path": str(model), "sha256": hashlib.sha256(model.read_bytes()).hexdigest()},
            "material": {"family": "synthetic test material", "grade_unknown_confirmed": True},
            "target": {"x_mm": 20, "y_mm": 15, "z_mm": 4, "z_tolerance_mm": 0.01},
            "stock": {"nominal_mm": {"x": 80, "y": 60, "z": 5.5}, "measurement": {
                "method": "top_bottom", "setup_id": "fixture-setup", "datum_id": "support",
                "measured_at": "synthetic-test", "evidence": "synthetic fixture only",
                "confirmed_current_setup": True, "top_z_mm": -82.7, "bottom_z_mm": -88,
                "interlayer_mm": 0.1, "thickness_mm": 5.2}},
            "thickness_control": {"strategy": "face_to_thickness", "target_mm": 4,
                "operation_ids": ["face"], "after_facing_reference": "remeasure then finished top"},
            "datum": {"description": "synthetic support datum"},
            "retention": {"method": "synthetic tabs", "remaining_stock_mm": 0,
                          "separation_plan": "synthetic remove tabs and finish"},
            "tools": [{"id": "mill", "kind": "endmill", "diameter_mm": 1.4,
                "shank_mm": 3.175, "flute_length_mm": 6.5, "material": "carbide",
                "flutes": 2, "geometry": "flat", "reach_evidence": "synthetic clearance"}],
            "operations": [self.operation("face", "face", 1.2), self.operation("outline", "contour", 4)],
            "plan_confirmed": True, "deliverables": {"nc": False}}
        self.job["operations"][0]["target_thickness_mm"] = 4
        self.job["operations"][1]["reference"] = "finished_top"
        # Synthetic future service for positive consistency tests only.
        self.caps = {"supported": list(MODULE.CAPABILITIES.values()) + ["simulation_result_verification"]}

    @staticmethod
    def operation(ident, kind, depth):
        return {"id": ident, "kind": kind, "tool_id": "mill", "depth_mm": depth,
                "geometry_region": "synthetic geometry", "reference": "explicit fixture datum",
                "parameter_basis": "test values, not cutting advice", "rpm": 10000,
                "feed_mm_min": 100, "plunge_mm_min": 30, "stepdown_mm": 0.05}

    def codes(self, result):
        return {i["code"] for i in result["missing"] + result["blocking"]}

    def test_missing_length_control_is_blocked(self):
        self.job["stock"]["measurement"] = {}
        self.job["thickness_control"] = {}
        self.job["operations"] = [self.operation("drill", "drill", 1), self.operation("outline", "contour", 0.1)]
        for stage in ("plan", "cam", "delivery"):
            result = validate_job(self.job, stage, self.caps)
            self.assertFalse(result["ready_for_requested_stage"])
            self.assertIn("length_uncontrolled", self.codes(result))

    def test_declaring_facing_without_operation_does_not_pass(self):
        self.job["operations"].pop(0)
        self.assertIn("facing_missing", self.codes(validate_job(self.job)))

    def test_facing_does_not_make_shallow_profile_complete(self):
        self.job["operations"][1]["depth_mm"] = 0.1
        self.assertIn("profile_incomplete", self.codes(validate_job(self.job)))

    def test_explicit_separation_allowance_and_finished_top_reference(self):
        self.job["retention"]["remaining_stock_mm"] = 0.2
        self.job["operations"][1]["depth_mm"] = 3.8
        self.assertTrue(validate_job(self.job)["ready_for_requested_stage"])
        self.job["operations"][1]["reference"] = "raw_top"
        self.assertIn("profile_reference", self.codes(validate_job(self.job)))

    def test_plan_can_precede_measurement_but_cam_cannot(self):
        self.job["stock"]["measurement"] = {}
        self.assertTrue(validate_job(self.job)["ready_for_requested_stage"])
        self.assertIn("actual_thickness_missing", self.codes(validate_job(self.job, "cam", self.caps)))

    def test_compensated_thickness_and_removal(self):
        result = validate_job(self.job, "cam", self.caps)
        self.assertTrue(result["ready_for_requested_stage"], result)
        self.assertAlmostEqual(result["derived_metrics"]["actual_thickness_mm"], 5.2)
        self.assertAlmostEqual(result["derived_metrics"]["thickness_removal_mm"], 1.2)

    def test_previous_setup_and_unknown_interlayer_fail(self):
        self.job["stock"]["measurement"]["setup_id"] = "old-point"
        self.job["stock"]["measurement"]["interlayer_mm"] = None
        codes = self.codes(validate_job(self.job, "cam", self.caps))
        self.assertIn("measurement_setup", codes)
        self.assertIn("measurement_coordinates", codes)

    def test_nominal_thickness_does_not_establish_finished_size(self):
        self.job["thickness_control"]["strategy"] = "use_verified_stock"
        self.job["operations"].pop(0)
        self.assertIn("stock_not_to_size", self.codes(validate_job(self.job, "cam", self.caps)))

    def test_actual_current_service_cannot_execute_facing(self):
        current_caps = {"supported": [MODULE.CAPABILITIES["drill"], MODULE.CAPABILITIES["contour"]]}
        result = validate_job(self.job, "cam", current_caps)
        self.assertIn("capability_missing", self.codes(result))
        self.assertFalse(result["ready_for_requested_stage"])

    def test_surface_engraving_does_not_force_facing(self):
        self.job["job_kind"] = "surface_engraving"
        self.job["target"] = {"engraving_depth_mm": 0.1}
        self.job["thickness_control"] = {}
        self.job["operations"] = [self.operation("engrave", "engrave", 0.1)]
        self.assertTrue(validate_job(self.job, "cam", self.caps)["ready_for_requested_stage"])

    def test_shallow_test_has_explicit_limit_and_cannot_enable_machine(self):
        self.job["scope"] = "cam_test"
        self.job["thickness_control"] = {}
        self.job["stock"]["measurement"] = {}
        self.job["operations"] = [self.operation("outline", "contour", 0.1)]
        self.job["test_limits_mm"] = {"outline": 0.1}
        result = validate_job(self.job, "cam", self.caps)
        self.assertTrue(result["ready_for_requested_stage"])
        self.assertTrue(result["warnings"])
        self.job["operations"][0]["depth_mm"] = 0.2
        self.assertIn("test_limit_exceeded", self.codes(validate_job(self.job, "cam", self.caps)))
        self.assertIn("machine_unsupported", self.codes(validate_job(self.job, "machine", self.caps)))

    def test_reach_and_model_changes_are_detected(self):
        self.job["operations"][1]["depth_mm"] = 6.5
        self.job["model"]["sha256"] = "changed"
        codes = self.codes(validate_job(self.job, "cam", self.caps))
        self.assertIn("tool_reach", codes)
        self.assertIn("model_changed", codes)

    def test_old_or_auxiliary_only_evidence_cannot_complete_delivery(self):
        evidence = {"revision": 0, "simulation_verified": True, "operations": []}
        result = validate_job(self.job, "delivery", self.caps, evidence)
        self.assertIn("stale_evidence", self.codes(result))
        self.assertIn("operation_evidence", self.codes(result))
        evidence["revision"] = 1
        evidence["operations"] = [{"id": op["id"], "calculated": True, "saved_verified": True,
            "parameters_match": True, "motion_path_verified": True} for op in self.job["operations"]]
        self.assertTrue(validate_job(self.job, "delivery", self.caps, evidence)["ready_for_requested_stage"])
        self.assertIn("simulation_unverified", self.codes(validate_job(self.job, "delivery", {"supported": list(MODULE.CAPABILITIES.values())}, evidence)))

    def test_cli_template_and_invalid_json_exit_codes(self):
        result = subprocess.run([sys.executable, str(SCRIPT), str(SKILL / "assets" / "job-template.json")], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["ready_for_requested_stage"])
        invalid = Path(self.temp.name) / "invalid.json"
        invalid.write_text("{", encoding="utf-8")
        result = subprocess.run([sys.executable, str(SCRIPT), str(invalid)], capture_output=True)
        self.assertEqual(result.returncode, 3)
        self.assertIn("invalid_input", json.loads(result.stdout))

    def test_discussion_is_read_only(self):
        self.job["scope"] = "discussion"
        self.assertFalse(validate_job(self.job, "cam", self.caps)["ready_for_requested_stage"])

    def generic_job(self, kind="contour"):
        self.job["job_kind"] = "custom"
        self.job["workflow"] = {"mode": "custom", "selection_source": "user_confirmed"}
        self.job["target"] = {}
        self.job["thickness_control"] = {}
        self.job["stock"]["measurement"] = {}
        self.job["operations"] = [self.operation("feature", kind, 0.5)]
        self.job["requirements"] = [{"id": "feature-size", "description": "chosen feature",
            "acceptance": "compare drawing and saved parameters", "resolution": "operations", "operation_ids": ["feature"]}]

    def test_custom_pocket_relief_and_chamfer_plans_do_not_require_facing(self):
        for kind in ("pocket", "relief_3d", "chamfer"):
            self.generic_job(kind)
            result = validate_job(self.job)
            self.assertTrue(result["ready_for_requested_stage"], result)
            # Planning a route never claims the real adapter implements it.
            self.assertIn("capability_missing", self.codes(validate_job(self.job, "cam", {"supported": []})))

    def test_custom_blind_hole_route_does_not_require_thickness_measurement(self):
        self.generic_job("drill")
        self.job["tools"][0]["kind"] = "drill"
        self.job["operations"][0]["hole_type"] = "blind"
        result = validate_job(self.job, "cam", self.caps)
        self.assertTrue(result["ready_for_requested_stage"], result)
        self.assertNotIn("actual_thickness_missing", self.codes(result))

    def test_custom_uncovered_goal_cannot_pass(self):
        self.generic_job()
        self.job["requirements"][0]["operation_ids"] = ["not-in-plan"]
        self.assertIn("goal_uncovered", self.codes(validate_job(self.job)))

    def test_user_can_choose_existing_condition_instead_of_machining_it(self):
        self.generic_job()
        self.job["requirements"].append({"id": "existing-finish", "description": "existing surface finish",
            "acceptance": "confirmed against drawing", "resolution": "verified_input",
            "evidence": "synthetic inspection report", "confirmed_current": True})
        self.assertTrue(validate_job(self.job)["ready_for_requested_stage"])
        self.job["requirements"][-1]["confirmed_current"] = False
        self.assertIn("input_unverified", self.codes(validate_job(self.job)))

    def test_custom_dependency_cycle_and_wrong_order_are_detected(self):
        self.generic_job()
        self.job["operations"].append(self.operation("second", "contour", 0.5))
        self.job["operations"][0]["depends_on"] = ["second"]
        self.job["operations"][1]["depends_on"] = ["feature"]
        codes = self.codes(validate_job(self.job))
        self.assertIn("dependency_cycle", codes)
        self.assertIn("dependency_order", codes)

    def test_multi_setup_requires_datum_transfer_description(self):
        self.generic_job()
        self.job["operations"][0]["changes_setup"] = True
        self.assertIn("setup_transition", self.codes(validate_job(self.job)))
        self.job["operations"][0]["reestablish_datum"] = "synthetic locating pins and verification"
        self.assertTrue(validate_job(self.job)["ready_for_requested_stage"])

    def test_manual_handoff_is_an_explicit_choice_with_separate_evidence(self):
        self.generic_job()
        self.job["operations"].insert(0, {"id": "external", "kind": "external-preparation",
            "execution_mode": "manual", "handoff": "user-selected external supplier",
            "acceptance": "inspect supplier report"})
        result = validate_job(self.job, "cam", self.caps)
        self.assertTrue(result["ready_for_requested_stage"], result)
        self.assertTrue(result["warnings"])
        evidence = {"revision": 1, "simulation_verified": True, "operations": [{"id": "feature",
            "calculated": True, "saved_verified": True, "parameters_match": True, "motion_path_verified": True}]}
        self.assertIn("manual_step_unverified", self.codes(validate_job(self.job, "delivery", self.caps, evidence)))
        evidence["manual_steps"] = [{"id": "external", "verified": True, "evidence": "synthetic external report"}]
        self.assertTrue(validate_job(self.job, "delivery", self.caps, evidence)["ready_for_requested_stage"])

    def test_excluding_requirement_requires_real_user_choice(self):
        self.generic_job()
        self.job["requirements"][0].update(resolution="excluded_by_user", reason="not requested", user_confirmed=False)
        self.assertIn("scope_exclusion", self.codes(validate_job(self.job)))


if __name__ == "__main__":
    unittest.main()
