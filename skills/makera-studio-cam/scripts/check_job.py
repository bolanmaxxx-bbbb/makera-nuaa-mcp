"""Read-only workflow consistency checks; never controls Studio or a machine."""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path


CAPABILITIES = {
    "face": "native_facing_to_thickness",
    "drill": "native_2d_drilling_and_verified_peck_retraction_sequence",
    "contour": "native_2d_contour_calculation_and_saved_vertex_verification",
    "engrave": "native_surface_engraving",
    "pocket": "native_pocket_calculation_and_verification",
    "relief_3d": "native_3d_relief_calculation_and_verification",
    "chamfer": "native_2d_chamfer_calculation_and_verification",
}
GROUPS = ["task", "stock", "length", "tools", "process", "confirmation", "capability", "evidence"]


def check_route(job, issue):
    """User-defined goals and dependencies; no compulsory machining recipe."""
    workflow = job.get("workflow", {})
    if workflow.get("mode") not in {"assisted", "template", "custom"}:
        issue("workflow_mode", "workflow.mode", "选择引导推荐、工艺模板或自定义流程", "task", True)
    if workflow.get("selection_source") != "user_confirmed":
        issue("route_choice", "workflow.selection_source", "流程需要用户选择/认可，不由模板自动代替选择", "confirmation", True)
    if workflow.get("mode") == "template" and not workflow.get("template"):
        issue("template_choice", "workflow.template", "记录所选工艺模板", "task", True)
    operations = job.get("operations", [])
    by_id = {op.get("id"): op for op in operations}
    seen, active = set(), set()

    def visit(ident):
        if ident in active:
            issue("dependency_cycle", "operations", "工序依赖存在循环", "process")
            return
        if ident in seen:
            return
        active.add(ident)
        for dependency in by_id[ident].get("depends_on", []):
            if dependency not in by_id:
                issue("dependency_missing", "operations." + str(ident), "依赖工序不存在: " + str(dependency), "process")
            else:
                visit(dependency)
                order = list(by_id)
                if order.index(dependency) >= order.index(ident):
                    issue("dependency_order", "operations." + str(ident), "工序顺序违反用户声明的前置条件", "process")
        active.remove(ident)
        seen.add(ident)

    for ident in by_id:
        visit(ident)
    requirements = job.get("requirements", [])
    if not requirements:
        issue("requirements_missing", "requirements", "先记录任务目标及各目标的验收方法", "task", True)
    goal_ids = set()
    for requirement in requirements:
        ident = requirement.get("id")
        if not isinstance(ident, str) or not ident or ident in goal_ids:
            issue("requirement_id", "requirements", "每项目标需要唯一id", "task")
        goal_ids.add(ident)
        for field in ("description", "acceptance"):
            value = requirement.get(field)
            if not isinstance(value, str) or not value.strip():
                issue("requirement_detail", "requirements." + str(ident) + "." + field, "说明目标和可检查的验收方式", "task", True)
        resolution = requirement.get("resolution")
        if resolution == "operations":
            links = requirement.get("operation_ids", [])
            if not links or any(link not in by_id for link in links):
                issue("goal_uncovered", "requirements." + str(ident), "目标尚无完整关联工序", "process")
        elif resolution == "verified_input":
            if not requirement.get("evidence") or requirement.get("confirmed_current") is not True:
                issue("input_unverified", "requirements." + str(ident), "使用现成条件满足目标时须记录当前有效依据", "stock", True)
        elif resolution == "excluded_by_user":
            if requirement.get("user_confirmed") is not True or not requirement.get("reason"):
                issue("scope_exclusion", "requirements." + str(ident), "缩减任务范围需要用户实际认可及原因", "confirmation", True)
        else:
            issue("goal_unresolved", "requirements." + str(ident), "选择关联工序、已验证输入，或用户明确排除", "process", True)
    changes = [op for op in operations if op.get("changes_setup") is True]
    for op in changes:
        if not op.get("reestablish_datum"):
            issue("setup_transition", "operations." + str(op.get("id")), "翻面/重新装夹后说明如何恢复定位及检查相关尺寸", "process", True)


def number(value, minimum=0, positive=False):
    return (isinstance(value, (float, int)) and not isinstance(value, bool)
            and math.isfinite(value) and (value > minimum if positive else value >= minimum))


def validate_job(job, stage="plan", capabilities=None, evidence=None):
    if not isinstance(job, dict):
        raise ValueError("Job must be a JSON object")
    if stage not in {"plan", "cam", "delivery", "machine"}:
        raise ValueError("Unknown stage")
    missing, blocking, warnings, metrics = [], [], [], {}

    def issue(code, path, message, group, absent=False):
        (missing if absent else blocking).append(
            {"code": code, "path": path, "message": message, "group": group})

    def require(value, path, group, numeric=False, positive=False):
        good = number(value, positive=positive) if numeric else isinstance(value, str) and bool(value.strip())
        if not good:
            issue("missing_or_invalid", path, "补充有效数值（mm、rpm或mm/min）" if numeric else "补充并记录来源",
                  group, absent=True)
        return good

    if job.get("schema_version") != 1:
        issue("schema", "schema_version", "需要schema_version=1", "task")
    scope = job.get("scope")
    if scope not in {"discussion", "cam_test", "production_cam"}:
        issue("scope", "scope", "先明确讨论、软件测试或正式CAM", "task", True)
    if scope == "discussion":
        return {"stage": stage, "scope": scope, "ready_for_requested_stage": False,
                "missing": missing, "blocking": blocking, "warnings": ["仅讨论，不授权写入或运动"],
                "derived_metrics": {}, "next_question_group": None}
    if job.get("scope_source") != "user_confirmed":
        issue("scope_source", "scope_source", "范围须来自用户实际确认，不能自动降级为测试", "task", True)
    if job.get("process") != "rotary" or job.get("units") != "mm":
        issue("unsupported_process", "process/units", "本校验器仅覆盖mm制旋转刀具工艺", "task")
    kind = job.get("job_kind")
    if kind not in {"plate_part", "surface_engraving", "custom"}:
        issue("dimension_chain", "job_kind", "选择可选板件/表面模板，或用custom记录独立目标与工序", "task")
    target, stock = job.get("target", {}), job.get("stock", {})
    material, model = job.get("material", {}), job.get("model", {})
    control = job.get("thickness_control", {})
    operations, tools = job.get("operations", []), job.get("tools", [])
    if not isinstance(operations, list) or not isinstance(tools, list):
        raise ValueError("operations and tools must be arrays")
    if kind == "custom":
        check_route(job, issue)
    require(material.get("family"), "material.family", "stock")
    if not material.get("grade") and material.get("grade_unknown_confirmed") is not True:
        issue("material_grade", "material.grade", "补充牌号，或记录用户明确不清楚", "stock", True)
    if kind == "plate_part" and scope == "production_cam":
        for axis in "xyz":
            require(target.get(axis + "_mm"), "target." + axis + "_mm", "task", True, True)
        require(target.get("z_tolerance_mm"), "target.z_tolerance_mm", "task", True, True)
        require(job.get("retention", {}).get("method"), "retention.method", "process")
        require(job.get("retention", {}).get("separation_plan"), "retention.separation_plan", "process")
    if kind == "surface_engraving":
        require(target.get("engraving_depth_mm"), "target.engraving_depth_mm", "task", True, True)
    if not operations:
        issue("operations", "operations", "补齐覆盖任务的工序，不以导入模型代替CAM", "process", True)
    ids = [op.get("id") for op in operations]
    tool_ids = [tool.get("id") for tool in tools]
    for values, path in [(ids, "operations"), (tool_ids, "tools")]:
        if any(not isinstance(v, str) or not v for v in values) or len(values) != len(set(values)):
            issue("ids", path, "工序和刀具需要各自唯一的非空字符串id", "process")
    tool_map = {tool.get("id"): tool for tool in tools}
    tol = target.get("z_tolerance_mm")
    tol = tol if number(tol, positive=True) else 0.000001
    target_z = target.get("z_mm")
    if kind == "plate_part" and scope == "production_cam":
        remaining = job.get("retention", {}).get("remaining_stock_mm")
        require(remaining, "retention.remaining_stock_mm", "process", True)
        contours = [op for op in operations if op.get("kind") == "contour"]
        if not contours:
            issue("profile_missing", "operations", "当前板件规则需要覆盖最终外形的轮廓工序", "process")
        elif number(target_z, positive=True) and number(remaining):
            if remaining >= target_z:
                issue("retention_depth", "retention.remaining_stock_mm", "保留量不能覆盖整个成品厚度", "process")
            for op in contours:
                if not number(op.get("depth_mm"), positive=True) or op["depth_mm"] + tol < target_z - remaining:
                    issue("profile_incomplete", "operations." + str(op.get("id")), "轮廓深度未覆盖成品厚度和明确的后续分离余量", "process")
                if op.get("reference") != "finished_top":
                    issue("profile_reference", "operations." + str(op.get("id")) + ".reference", "当前单面尺寸链要求轮廓深度从定厚后的顶面计", "process")
        strategy = control.get("strategy")
        if strategy not in {"face_to_thickness", "use_verified_stock", "backside_finish"}:
            issue("length_uncontrolled", "thickness_control.strategy",
                  "成品厚度尚无形成方式；名义毛坯厚度和浅层刀路不能替代", "length", True)
        elif strategy == "backside_finish":
            issue("backside_chain", "thickness_control", "当前校验器未覆盖翻面尺寸链，须扩展验证后执行", "length")
        if strategy == "face_to_thickness":
            face_ids = control.get("operation_ids", [])
            faces = [op for op in operations if op.get("id") in face_ids and op.get("kind") == "face"]
            if not face_ids or len(faces) != len(face_ids):
                issue("facing_missing", "thickness_control.operation_ids", "完整计划缺少关联的面铣定厚工序", "length")
            require(control.get("after_facing_reference"), "thickness_control.after_facing_reference", "length")
            if not number(target_z, positive=True) or not number(control.get("target_mm"), positive=True) or abs(control["target_mm"] - target_z) > tol:
                issue("length_target_mismatch", "thickness_control.target_mm", "定厚目标必须对应成品板厚方向尺寸", "length")
            for face in faces:
                if not number(face.get("target_thickness_mm"), positive=True) or not number(target_z, positive=True) or abs(face["target_thickness_mm"] - target_z) > tol:
                    issue("face_target", "operations." + face["id"], "面铣工序须对应目标厚度", "length")
            if faces:
                last_face = max(ids.index(op["id"]) for op in faces)
                if any(i < last_face and op.get("kind") in {"drill", "contour"} for i, op in enumerate(operations)) and not job.get("workflow", {}).get("order_override_reason"):
                    issue("operation_order", "operations", "当前单面板件规则要求先定厚，再钻孔和外轮廓；其他顺序须单独论证", "process")
    if scope == "cam_test":
        warnings.append("仅验收用户指定的软件测试；不能据此声称完整零件、仿真通过或可上机")
        limits = job.get("test_limits_mm", {})
        for op in operations:
            if op.get("execution_mode") == "manual":
                continue
            limit, depth = limits.get(op.get("id")), op.get("depth_mm")
            if not number(limit, positive=True):
                issue("test_limit", "test_limits_mm", "每条测试工序需要用户确认的最大切深", "task", True)
            elif number(depth, positive=True) and depth > limit:
                issue("test_limit_exceeded", "operations." + str(op.get("id")), "切深超出本次软件测试范围", "process")

    executing = stage in {"cam", "delivery", "machine"}
    if executing:
        if job.get("plan_confirmed") is not True:
            issue("plan_confirmation", "plan_confirmed", "须先得到用户对完整工艺的实际认可", "confirmation", True)
        require(job.get("setup_id"), "setup_id", "stock")
        require(job.get("datum", {}).get("description"), "datum.description", "stock")
        for axis in "xyz":
            require(stock.get("nominal_mm", {}).get(axis), "stock.nominal_mm." + axis, "stock", True, True)
        path, expected_hash = model.get("path"), model.get("sha256")
        if not path or not expected_hash or not Path(path).is_file():
            issue("model_evidence", "model", "读取实际模型文件并记录SHA256", "task", True)
        elif hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected_hash:
            issue("model_changed", "model.sha256", "模型已变化，旧计划与下游证据须重新核对", "task")
        measurement = stock.get("measurement", {})
        thickness = None
        if scope == "production_cam" and kind == "plate_part":
            for field in ("method", "datum_id", "measured_at", "evidence"):
                require(measurement.get(field), "stock.measurement." + field, "stock")
            if not job.get("setup_id") or measurement.get("setup_id") != job.get("setup_id") or measurement.get("confirmed_current_setup") is not True:
                issue("measurement_setup", "stock.measurement", "实测记录须属于当前装夹和当前基准；旧测点不能沿用", "stock")
            if measurement.get("method") == "top_bottom":
                top, bottom, interlayer = (measurement.get(k) for k in ("top_z_mm", "bottom_z_mm", "interlayer_mm"))
                coordinates_ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (top, bottom))
                if not coordinates_ok or not number(interlayer):
                    issue("measurement_coordinates", "stock.measurement", "需要同一体系的顶面、承托面及已知垫层，未知不能填0", "stock", True)
                else:
                    thickness = top - bottom - interlayer
                    recorded = measurement.get("thickness_mm")
                    if recorded is not None and (not number(recorded, positive=True) or abs(recorded - thickness) > tol):
                        issue("measurement_conflict", "stock.measurement.thickness_mm", "记录厚度与坐标及垫层补偿结果不一致", "stock")
            elif measurement.get("method") == "direct":
                thickness = measurement.get("thickness_mm")
            else:
                issue("measurement_method", "stock.measurement.method", "选择direct或top_bottom，并提供实际测量证据", "stock", True)
            if not number(thickness, positive=True):
                issue("actual_thickness_missing", "stock.measurement", "正式CAM不能用名义毛坯厚度替代实测厚度", "stock", True)
            else:
                metrics["actual_thickness_mm"] = thickness
                if kind == "surface_engraving" and number(target.get("engraving_depth_mm"), positive=True) and target["engraving_depth_mm"] >= thickness:
                    issue("engraving_through", "target.engraving_depth_mm", "雕刻深度达到或超过实测厚度，需重新明确任务", "length")
                if kind == "plate_part" and number(target_z, positive=True):
                    removal = thickness - target_z
                    metrics["thickness_removal_mm"] = removal
                    if removal < -tol:
                        issue("stock_too_thin", "target.z_mm", "当前材料小于成品厚度", "length")
                    if control.get("strategy") == "use_verified_stock" and abs(removal) > tol:
                        issue("stock_not_to_size", "thickness_control", "实测厚度不在成品公差内，不能采用已等厚原料方案", "length")
                    if control.get("strategy") == "face_to_thickness":
                        faces = [op for op in operations if op.get("id") in control.get("operation_ids", []) and op.get("kind") == "face"]
                        total = sum(op.get("depth_mm", 0) for op in faces if number(op.get("depth_mm")))
                        if removal <= 0 or abs(total - removal) > tol:
                            issue("face_removal_mismatch", "operations", "面铣总去除量须等于实测厚度减目标厚度", "length")
        for op in operations:
            prefix = "operations." + str(op.get("id"))
            op_kind = op.get("kind")
            if op.get("execution_mode") == "manual":
                require(op.get("handoff"), prefix + ".handoff", "process")
                require(op.get("acceptance"), prefix + ".acceptance", "process")
                warnings.append(prefix + "为人工/外部步骤；CAM计算就绪不表示此步骤已完成")
                continue
            if op_kind not in CAPABILITIES:
                issue("operation_kind", prefix + ".kind", "工序类型尚未覆盖", "process")
            for field in ("geometry_region", "reference", "parameter_basis"):
                require(op.get(field), prefix + "." + field, "process")
            for field in ("depth_mm", "rpm", "feed_mm_min", "plunge_mm_min", "stepdown_mm"):
                require(op.get(field), prefix + "." + field, "process", True, True)
            tool = tool_map.get(op.get("tool_id"))
            if tool is None:
                issue("tool_missing", prefix + ".tool_id", "映射到完整记录的实际刀具", "tools", True)
                continue
            for field in ("diameter_mm", "shank_mm", "flute_length_mm"):
                require(tool.get(field), "tools." + str(tool.get("id")) + "." + field, "tools", True, True)
            for field in ("material", "geometry", "reach_evidence"):
                require(tool.get(field), "tools." + str(tool.get("id")) + "." + field, "tools")
            if op_kind != "drill":
                require(tool.get("flutes"), "tools." + str(tool.get("id")) + ".flutes", "tools", True, True)
            if op_kind == "drill" and tool.get("kind") != "drill":
                issue("tool_kind", prefix, "钻孔工序不能使用未确认类型的刀具", "tools")
            if op_kind != "drill" and tool.get("kind") not in {"endmill", "engraver"}:
                issue("tool_kind", prefix, "铣削/雕刻工序需要对应的刀具类型", "tools")
            if op_kind == "chamfer":
                chamfer = op.get("chamfer", {})
                if not isinstance(chamfer,dict):
                    chamfer = {}
                if chamfer.get("side") not in {"inside","outside"} or chamfer.get("direction") not in {"climb","conventional"}:
                    issue("chamfer_strategy", prefix + ".chamfer", "明确内外侧和顺逆铣", "process")
                tip_offset,angle,tip = chamfer.get("tip_offset_mm"),tool.get("included_angle_deg"),tool.get("tip_diameter_mm")
                if not number(tip_offset) or tip_offset>2:
                    issue("chamfer_tip_offset", prefix + ".chamfer.tip_offset_mm", "明确0到2mm端部偏移", "process")
                chamfer_depth = chamfer.get('depth_mm')
                if not number(chamfer_depth,positive=True) or (number(op.get('depth_mm')) and number(op.get('start_depth_mm',0)) and number(chamfer_depth) and abs(op['depth_mm']-op.get('start_depth_mm',0)-chamfer_depth)>1e-6):
                    issue('chamfer_depth_basis',prefix+'.chamfer.depth_mm','明确倒角深度；工序depth_mm须等于start_depth_mm加倒角深度，不直接传为原生endDepth','process')
                if tool.get("kind") != "engraver" or tool.get("geometry") != "v" or not number(angle,positive=True) or angle>=180 or not number(tip) or (number(tool.get("diameter_mm")) and tip>=tool["diameter_mm"]):
                    issue("chamfer_tool", prefix, "倒角需V刀及有效夹角、尖端直径", "tools")
                elif abs(angle-90)>1e-6:
                    issue('chamfer_tool_scope',prefix,'当前已验证倒角接口仅接受90度V刀；其他角度待独立验证','tools')
                elif all(number(v) for v in (tip_offset,op.get("depth_mm"),op.get("start_depth_mm",0),tool.get("flute_length_mm"),tool.get("diameter_mm"))):
                    cone = (tool["diameter_mm"]-tip)/(2*math.tan(math.radians(angle/2)))
                    if op["depth_mm"]-op.get("start_depth_mm",0)+tip_offset>min(cone,tool["flute_length_mm"])+1e-8:
                        issue("chamfer_tool_reach", prefix, "倒角深度加端部偏移超过有效锥刃", "tools")
                    if op['depth_mm']+tip_offset>tool['flute_length_mm']+1e-8:
                        issue('chamfer_flute_reach',prefix,'起始深度、倒角量和端部偏移总和超过有效刃长','tools')
                    nominal_height = stock.get("nominal_mm",{}).get("z")
                    if number(nominal_height) and op["depth_mm"]+tip_offset>nominal_height:
                        issue("chamfer_stock_depth", prefix, "倒角刀尖深度超过毛坯", "process")
                if op.get("through_cut_allowance_mm",0):
                    issue("chamfer_overcut", prefix, "当前倒角接口不支持穿透垫板余量", "process")
            depth, reach = op.get("depth_mm"), tool.get("flute_length_mm")
            start_depth = op.get("start_depth_mm", 0)
            if not number(start_depth) or (number(depth, positive=True) and start_depth >= depth):
                issue("depth_interval", prefix + ".start_depth_mm", "起始深度须为有限非负值，且小于结束深度", "process")
            if number(depth, positive=True) and number(reach, positive=True) and depth >= reach:
                issue("tool_reach", prefix, "切深达到/超过刃长；还需核对实际伸出、肩部和夹持间隙", "tools")
            if number(depth, positive=True) and number(op.get("stepdown_mm"), positive=True) and op["stepdown_mm"] > depth:
                issue("stepdown", prefix, "每层深度超出该工序总切深", "process")
            if scope == "production_cam" and op_kind == "drill":
                hole_type = op.get("hole_type")
                if hole_type not in {"blind", "through"}:
                    issue("hole_type", prefix, "确认盲孔或通孔", "process", True)
                if hole_type == "through" and kind == "plate_part":
                    if op.get("reference") != "finished_top":
                        issue("drilling_reference", prefix + ".reference", "当前贯穿行程公式以定厚后的顶面为参考", "process")
                    for field in ("tip_angle_deg", "breakthrough_mm", "spoil_allowance_mm"):
                        require(op.get(field), prefix + "." + field, "process", True, True)
                    angle = op.get("tip_angle_deg")
                    if number(angle, positive=True) and angle < 180 and number(tool.get("diameter_mm"), positive=True) and number(target_z, positive=True):
                        tip = tool["diameter_mm"] / 2 / math.tan(math.radians(angle / 2))
                        metrics[prefix + ".tip_length_mm"] = tip
                        extra = op.get("breakthrough_mm")
                        allowance = op.get("spoil_allowance_mm")
                        if number(extra, positive=True) and number(allowance, positive=True) and number(depth, positive=True):
                            if depth + tol < target_z + tip + extra or depth - target_z > allowance + tol:
                                issue("through_depth", prefix, "须覆盖完整孔径贯穿行程，并在已确认垫板余量内", "process")
                    else:
                        issue("tip_geometry", prefix, "钻尖角应在0到180度之间，并有已知孔径和板厚", "tools")
                elif hole_type == "through":
                    require(op.get("through_depth_basis"), prefix + ".through_depth_basis", "process")
        supported = set((capabilities or {}).get("supported", []))
        if not capabilities:
            issue("capabilities_missing", "capabilities", "先读取当前挂载服务get_capabilities，不能使用代码版本推定", "capability", True)
        for op in operations:
            if op.get("execution_mode") == "manual":
                continue
            required = CAPABILITIES.get(op.get("kind"))
            if required and required not in supported:
                issue("capability_missing", "operations." + str(op.get("id")), "当前MCP未验证所需能力: " + required, "capability")
    else:
        warnings.append("计划可先讨论；进入CAM仍需当前装夹测量、实际参数、授权及服务能力")

    if stage in {"delivery", "machine"}:
        evidence = evidence or {}
        revision = job.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1 or evidence.get("revision") != revision:
            issue("stale_evidence", "evidence.revision", "仅接受当前任务修订的证据", "evidence")
        records = {record.get("id"): record for record in evidence.get("operations", [])}
        manual_records = {record.get("id"): record for record in evidence.get("manual_steps", [])}
        for op in operations:
            if op.get("execution_mode") == "manual":
                record = manual_records.get(op.get("id"), {})
                if record.get("verified") is not True or not record.get("evidence"):
                    issue("manual_step_unverified", "evidence." + str(op.get("id")), "人工/外部步骤需要实际交接验收记录，不能冒充MCP完成", "evidence")
                continue
            record = records.get(op.get("id"), {})
            for field in ("calculated", "saved_verified", "parameters_match", "motion_path_verified"):
                if record.get(field) is not True:
                    issue("operation_evidence", "evidence." + str(op.get("id")) + "." + field, "需要实际生成及保存回读证据", "evidence")
        if scope == "production_cam":
            if evidence.get("simulation_verified") is not True or "simulation_result_verification" not in set((capabilities or {}).get("supported", [])):
                issue("simulation_unverified", "evidence.simulation_verified", "正式CAM交付还缺受支持的实际仿真验证", "evidence")
        if job.get("deliverables", {}).get("nc") is True:
            if evidence.get("nc_verified") is not True or "nc_postprocess_and_verification" not in set((capabilities or {}).get("supported", [])):
                issue("nc_unverified", "evidence.nc_verified", "所需NC交付未得到后处理与校验证据", "evidence")
    if stage == "machine":
        issue("machine_unsupported", "stage", "本技能及当前服务不实现现场运动审批或执行", "capability")
    combined = missing + blocking
    first = next((group for group in GROUPS if any(item["group"] == group for item in combined)), None)
    return {"stage": stage, "scope": scope, "ready_for_requested_stage": not combined,
            "missing": missing, "blocking": blocking, "warnings": warnings,
            "derived_metrics": metrics, "next_question_group": first,
            "next_items": [item for item in combined if item["group"] == first][:3]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("--stage", choices=["plan", "cam", "delivery", "machine"], default="plan")
    parser.add_argument("--capabilities", type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    try:
        read = lambda path: json.loads(path.read_text(encoding="utf-8-sig")) if path else None
        result = validate_job(read(args.job), args.stage, read(args.capabilities), read(args.evidence))
        status = 0 if result["ready_for_requested_stage"] else 2
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        result, status = {"ready_for_requested_stage": False, "invalid_input": str(exc)}, 3
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
