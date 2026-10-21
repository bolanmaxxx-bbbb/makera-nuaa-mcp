"""Validate recorded local probe contacts; print JSON only, never send machine commands."""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import re
import statistics
import sys


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("measured_at must be an ISO timestamp with timezone")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("measured_at requires a timezone")
    return result


def latched_xyz(sample, report_format):
    """Import formats only: choosing grbl_prb does not establish firmware compatibility."""
    raw = sample.get("raw_report")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("raw_report evidence is required")
    if report_format == "grbl_prb":
        reports = re.findall(r"\[PRB:([^\[\]]+)\]", raw)
        if len(reports) != 1 or raw.count("[PRB:") != 1:
            raise ValueError("expected exactly one complete PRB report")
        parts = reports[0].split(":")
        if len(parts) != 2 or parts[1] != "1":
            raise ValueError("probe did not report a successful contact")
        values = [float(item) for item in parts[0].split(",")]
        if len(values) not in (3, 4) or not all(finite(v) for v in values):
            raise ValueError("PRB requires finite XYZ and optional A")
        if re.search(r"\b(ALARM|error)\b", raw, re.IGNORECASE):
            raise ValueError("probe acquisition contains a controller error")
        return dict(zip(("x", "y", "z", "a"), values))
    if report_format == "latched_xyz":
        xyz = sample.get("latched_xyz_mm")
        if sample.get("trigger_success") is not True or not isinstance(xyz, dict):
            raise ValueError("verified latched_xyz and trigger_success are required")
        if not all(finite(xyz.get(axis)) for axis in ("x", "y", "z")):
            raise ValueError("latched XYZ must be finite numbers")
        if "a" in xyz and not finite(xyz["a"]):
            raise ValueError("optional latched A must be finite")
        return {axis: xyz[axis] for axis in ("x", "y", "z", "a") if axis in xyz}
    raise ValueError("report_format must be grbl_prb or latched_xyz")


def validate_measurement(record):
    """Check supplied evidence, not physical truth, collision clearance or motion readiness."""
    if not isinstance(record, dict):
        raise ValueError("record must be an object")
    errors, pending = [], []
    result = {"record_valid": False, "measurement_usable": False,
              "machine_commands_sent": False, "errors": errors, "pending": pending,
              "derived": {}, "stock_measurement": None}
    context = record.get("context")
    if not isinstance(context, dict):
        raise ValueError("context must be an object")
    if type(record.get("schema_version")) is not int or record["schema_version"] != 1:
        errors.append("unsupported schema_version")
    for key in ("id", "setup_id", "datum_id", "coordinate_epoch", "probe_id", "calibration_id"):
        if not isinstance(context.get(key), str) or not context[key].strip():
            errors.append("context." + key + " is required")
    if context.get("units") != "mm" or context.get("z_positive_up") is not True:
        errors.append("requires mm and Z positive up")
    if context.get("frame") not in ("machine", "work"):
        errors.append("coordinate frame must be explicit")
    if context.get("frame") == "work" and not context.get("work_offset_revision"):
        errors.append("work coordinates require work_offset_revision")
    if context.get("report_frame_verified") is not True:
        errors.append("latched-report coordinate frame is not verified")
    if context.get("report_format") not in ("grbl_prb", "latched_xyz"):
        errors.append("unsupported latched-report import format")
    if context.get("probe_calibration_current") is not True:
        pending.append("current probe calibration and signal test need confirmation")
    if context.get("confirmed_current_setup") is not True:
        pending.append("current setup/context must be confirmed before using this result")

    points = record.get("points")
    if not isinstance(points, dict):
        raise ValueError("points must be an object")
    for surface in ("backing_top", "stock_top"):
        xy = points.get(surface)
        if not isinstance(xy, dict) or not all(finite(xy.get(k)) for k in ("x", "y")):
            errors.append(surface + " needs explicit finite XY in the report frame")
    xy_tolerance = record.get("xy_tolerance_mm")
    if not finite(xy_tolerance) or xy_tolerance < 0:
        errors.append("xy_tolerance_mm must be explicit and nonnegative")

    readings = record.get("readings")
    if not isinstance(readings, list):
        raise ValueError("readings must be an array")
    contacts = {"backing_top": [], "stock_top": []}
    times, previous_time, seen_ids = [], None, set()
    rotary_pose = []
    for index, sample in enumerate(readings):
        try:
            if not isinstance(sample, dict):
                raise ValueError("reading must be an object")
            ident = sample.get("id")
            if not isinstance(ident, str) or not ident.strip() or ident in seen_ids:
                raise ValueError("reading needs a unique acquisition id")
            seen_ids.add(ident)
            if sample.get("context_id") != context.get("id"):
                raise ValueError("contact belongs to a different setup/probe/coordinate context")
            if sample.get("controller_state_after") != "Idle":
                raise ValueError("Idle after acquisition was not confirmed")
            if sample.get("acquisition_ok") is not True:
                raise ValueError("acquisition needs explicit success/no-alarm confirmation")
            surface = sample.get("surface")
            if surface not in contacts:
                raise ValueError("surface must be backing_top or stock_top")
            at = timestamp(sample.get("measured_at"))
            if previous_time is not None and at <= previous_time:
                raise ValueError("contacts must have distinct increasing acquisition times")
            previous_time = at
            xyz = latched_xyz(sample, context.get("report_format"))
            # Equal XYZ are legitimate repeats, but a rotated setup is a different height chain.
            pose = ("a" in xyz, xyz.get("a"))
            if rotary_pose and pose != rotary_pose[0]:
                raise ValueError("rotary A changed or its report presence is inconsistent")
            rotary_pose.append(pose)
            xy = points.get(surface, {})
            if (finite(xy_tolerance) and isinstance(xy, dict)
                    and all(finite(xy.get(k)) for k in ("x", "y"))
                    and any(abs(xyz[k] - xy[k]) > xy_tolerance for k in ("x", "y"))):
                raise ValueError("latched XY does not match the designated surface point")
            contacts[surface].append(xyz["z"])
            times.append(at)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"readings[{index}]: {exc}")
    if not all(contacts.values()):
        errors.append("both backing_top and stock_top need successful contacts")
    if errors:
        return result

    derived = result["derived"]
    for surface, values in contacts.items():
        derived[surface] = {"count": len(values), "median_z_mm": statistics.median(values),
                            "spread_mm": max(values) - min(values)}
    if any(not finite(item[k]) for item in derived.values() for k in ("median_z_mm", "spread_mm")):
        derived.clear()
        errors.append("contact statistics exceed finite numeric range")
        return result
    minimum = record.get("minimum_contacts_per_surface")
    spread_limit = record.get("max_repeat_spread_mm")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 2:
        pending.append("choose a repeatability plan with at least two contacts per surface")
    elif any(len(v) < minimum for v in contacts.values()):
        pending.append("repeat contacts are incomplete")
    if not finite(spread_limit) or spread_limit < 0:
        pending.append("application-specific max_repeat_spread_mm is required")
    elif any(item["spread_mm"] > spread_limit + 1e-12 for item in derived.values()):
        pending.append("repeatability spread exceeds the selected limit")

    correction = record.get("backing_transfer", {})
    interlayer = record.get("interlayer", {})
    if not isinstance(correction, dict) or not isinstance(interlayer, dict):
        raise ValueError("backing_transfer and interlayer must be objects")
    delta, layer = correction.get("delta_z_mm"), interlayer.get("thickness_mm")
    if not finite(delta) or not correction.get("basis"):
        pending.append("backing height transfer to target needs a value and evidence, including zero")
    if not finite(layer) or layer < 0 or not interlayer.get("basis"):
        pending.append("interlayer/gap needs a nonnegative value and evidence, including zero")
    if finite(delta) and finite(layer) and layer >= 0:
        top = derived["stock_top"]["median_z_mm"]
        bottom = derived["backing_top"]["median_z_mm"] + delta
        thickness = top - bottom - layer
        if not finite(bottom) or not finite(thickness):
            errors.append("derived thickness exceeds finite numeric range")
        else:
            derived.update({"backing_at_target_z_mm": bottom, "thickness_mm": thickness})
        if thickness <= 0:
            errors.append("derived thickness must be positive; check frames, surfaces and compensation")

    quality = record.get("fitness", {})
    if not isinstance(quality, dict):
        raise ValueError("fitness must be an object")
    uncertainty, allowed = quality.get("estimated_error_bound_mm"), quality.get("allowed_error_mm")
    if (not finite(uncertainty) or uncertainty < 0 or not finite(allowed) or allowed <= 0
            or not quality.get("basis")):
        pending.append("measurement fitness needs an error bound, allowed error and basis")
    elif uncertainty > allowed:
        pending.append("estimated measurement error exceeds the application allowance")
    evidence = record.get("evidence")
    if not isinstance(evidence, str) or not evidence.strip():
        pending.append("retained raw measurement evidence location is required")
    result["record_valid"] = not errors
    result["measurement_usable"] = not errors and not pending
    if result["measurement_usable"]:
        result["stock_measurement"] = {
            "method": "top_bottom", "setup_id": context["setup_id"], "datum_id": context["datum_id"],
            "measured_at": max(times).isoformat(), "confirmed_current_setup": True,
            "evidence": evidence, "top_z_mm": top, "bottom_z_mm": bottom,
            "interlayer_mm": layer, "thickness_mm": thickness,
            "measurement_context": context.copy(), "points": points,
            "backing_transfer": correction, "fitness": quality,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path)
    args = parser.parse_args()
    try:
        result = validate_measurement(json.loads(args.record.read_text(encoding="utf-8-sig")))
        status = 0 if result["measurement_usable"] else 2
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        result = {"measurement_usable": False, "machine_commands_sent": False, "invalid_input": str(exc)}
        status = 3
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
