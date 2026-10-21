"""Native operation taxonomy; catalog presence is separate from execution support."""

OPERATIONS = [
    (0, 'pocket_2d', '2D区域清理', 'planar_milling', 'closed_svg_regions', 'flat_endmill',
     ['depth_interval','boundary_islands','stepdown','stepover','rough_finish_allowance'], 'nested_pocket', 'create_2d_pocket'),
    (1, 'contour_2d', '2D轮廓', 'planar_milling', 'svg_contours', 'flat_endmill',
     ['inside_outside_on','depth_interval','lead_in_out','tabs_retention'], 'inside_outside_tabs', 'create_2d_contour'),
    (2, 'drill_2d', '2D钻孔', 'planar_milling', 'svg_hole_locations', 'drill',
     ['hole_diameter','depth_and_tip','peck_retraction','backing_allowance'], 'hole_array', 'create_2d_drilling'),
    (9, 'thread_2d', '2D螺纹', 'planar_milling', 'svg_thread_locations', 'thread_mill',
     ['major_diameter','pitch','internal_external','handedness','pre_hole','thread_depth'], 'internal_external_thread', None),
    (13, 'chamfer_2d', '2D倒角', 'planar_milling', 'svg_edges', 'chamfer_tool',
     ['tool_angle_tip','inside_outside','chamfer_size_depth','tip_offset'], 'inside_outside_chamfer', 'create_2d_chamfer'),
    (6, 'relief_3d', '3D浮雕', 'model_milling', 'verified_model_mesh', 'flat_or_ball_endmill',
     ['model_pose','layered_or_surface','depth_allowance','bounding','stepover_scallop'], 'curved_heightfield', 'create_3d_relief'),
    (7, 'feature_3d', '3D特征加工', 'model_milling', 'model_faces_and_features', 'flat_endmill',
     ['feature_selection','axial_radial_allowance','rough_finish','pocket_strategy'], 'stepped_pocket_island', None),
    (8, 'contour_3d', '3D轮廓', 'model_milling', 'model_or_extracted_contours', 'flat_endmill',
     ['model_vs_contour_selection','start_end_offset','containment','tabs_retention'], 'stepped_model_silhouette', None),
    (10, 'drill_3d', '3D钻孔', 'model_milling', 'model_hole_features', 'drill',
     ['hole_feature_axis','diameter_depth','tip','peck_retraction'], 'model_hole_array', None),
    (14, 'chamfer_3d', '3D倒角', 'model_milling', 'model_edges_or_contours', 'chamfer_tool',
     ['edge_selection','existing_chamfer','tool_angle_tip','size_clearance'], 'model_edge_chamfer', None),
    (12, 'thread_3d', '3D螺纹', 'model_milling', 'model_thread_hole_features', 'thread_mill',
     ['feature_axis','major_diameter_pitch','handedness','pre_hole','thread_depth'], 'model_thread_hole', None),
    (11, 'rotary_relief', '旋转雕刻', 'rotary_milling', 'model_in_rotary_setup', 'flat_or_ball_endmill',
     ['rotary_axis_and_origin','stock_shape','tailstock_envelope','direction','allowance'], 'rotary_lobed_body', None),
    (3, 'laser_image', '激光图片', 'laser', 'raster_image', 'verified_laser_module',
     ['image_scale','power_speed','line_interval_dpi','scan_direction','focus_passes'], 'raster_gray_ramp', None),
    (4, 'laser_vector', '激光矢量', 'laser', 'svg_vectors', 'verified_laser_module',
     ['line_fill_offset_mode','power_speed','line_interval','focus_passes'], 'vector_line_and_fill', None),
]


def operation_catalog(native_labels):
    observed = set(native_labels)
    entries = []
    for native_type, ident, label, category, geometry, tool, parameters, case, creation in OPERATIONS:
        entries.append({'id':ident,'native_type':native_type,'native_label':label,'category':category,
                        'geometry_requirement':geometry,'tool_requirement':tool,'parameter_groups':parameters,
                        'validation_case':case,'available_in_native_chooser':label in observed,
                        'native_creation_saved_verified':True,
                        'mcp_creation_tool':creation,
                        'mcp_calculation_supported':creation is not None and label in observed,
                        'boundary':('3D relief currently supports a single model and verified flat tools only'
                                    if native_type == 6 else '2D SVG edges with existing 90-degree V tools; no V registration or 3D edges'
                                    if native_type == 13 else 'Only the advertised MCP parameter subset is executable'
                                    if creation else 'Native type cataloged; selection/parameter/calculation adapter not yet verified'),
                        'simulation_verified':False,'nc_export_verified':False,'machine_commands_sent':False})
    return {'operations':entries,'native_labels':native_labels,
            'unknown_native_labels':sorted(observed-{row[2] for row in OPERATIONS}),
            'classification_scope':'all 14 native chooser types created and saved in isolated development copies',
            'catalog_is_execution_permission':False,'machine_commands_sent':False}
