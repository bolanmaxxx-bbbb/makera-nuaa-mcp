# 持久任务与实际工具参数

适用于创建/继续任务、参数修订，以及把技能记录变成MCP调用。这里的helper只输出JSON，不自动调用工具、不批准运动。使用当前服务的真实工具schema核对参数，逐条调用并验收，不把整个输出当成可盲目执行的批处理。

## 任务工具全部接入

1. 已有task_id时直接get_cam_task(task_id)，没有id而用户要继续任务时才list_cam_tasks()；不要无故读取其他任务的内容。
2. 新任务用mcp_workflow.py task-spec job.json生成specification，传给create_cam_task(specification)。保存返回的id及revision到job.server_task。
3. get_next_cam_action(task_id)提示持久记录的下一阶段。inputs_checked、ready_for_desktop_execution仅表示该任务记录的基本输入检查，不代替技能工艺检查或已完成加工。
4. 参数发生变化时，用完整的新specification调用revise_cam_task(task_id, specification)，不是传一个局部patch。随后get_cam_task核对返回revision、下游invalidated及缺项，再更新job记录和受影响证据。
5. TaskStore当前没有公开的阶段完成写入工具，原生CAM操作也不会自动推进其stages。因此不能反复调用next直到完成，不能把pending抹掉，也不能直接改任务文件伪造完成。另存同修订的原生返回与文件回读证据，交付时如实解释记录和实际结果的差异。

技能job与TaskStore的schema不同：工具kind映射为type；geometry_region映射为region；model.path映射为model_path；毛坯、原点、output_directory等也需映射。原始requirements、workflow、测量、工序顺序等保留在specification.workflow_job，不能转换时丢弃。人工步骤保留在完整job里并列入unresolved_inputs，避免被自动步骤覆盖后误判完整任务。

job.revision和服务task.revision分别记录。服务create总是从1开始，不能假定两者数值天然相同；用source_job_revision和server_task保存对应关系。每次修订、重新绑定或改变模型都核对该关系，不沿用旧结果。

## 当前连接能力核对

保存本次get_capabilities结果和MCP工具列表（tools/list）为capabilities.json和tools.json。若当前宿主不能导出tools/list，可依据实际暴露工具列表形成名称数组；独立客户端的列表只证明那个新进程，不能冒充当前挂载进程已更新。

```text
python <skill>/scripts/mcp_workflow.py audit --capabilities capabilities.json --tools tools.json
```

assets/mcp-tool-map.json覆盖已验证版本的36个工具，按任务路由到对应参考资料。审核会指出新增但尚未接入的工具、未暴露工具，以及工具出现但能力声明不匹配的情况。服务版本只是线索：只调用当前工具列表和能力声明共同支持的动作。旧连接可继续其支持的只读检查；注册刀具、钻孔、区域清理等缺失功能先重连相应MCP服务，再重新核对。不要用新建独立进程的结果宣称当前聊天已重连。

v0.6的模型导入和完整毛坯配置按mcp-execution.md逐条调用；operation-calls不会自动替用户选择材料、猜测单位、导入模型或摆放布局。先验收新几何UUID与尺寸、stock保存结果，刷新binding.project_snapshot及文件哈希后，再映射工序参数。导入3D模型不产生可用于2D创建接口的SVG UUID，也不自动解锁三维工序。

v0.7的set_studio_model_placement需要原生网格UUID和当前MKS的SHA256，不使用任务里的源STL哈希代替项目哈希。摆放后重开核对模型矩阵/中心，刷新快照与受影响证据，再进入工序映射。旋转是相对增量，恢复任务时先读取现状，不重复回放旧参数；模型只是参考时，也不能假称既有二维工序已自动绑定它。

## 创建与配置工序分开

把当前inspect_studio_project返回值作为binding.project_snapshot，并设置project_path、job_revision和所选geometry_uuids。geometry_uuids必须来自该快照的svg_geometry；快照sha256必须仍匹配文件。技术绑定由Codex读取生成，不要求用户填写。

首次创建不设operation_index：

```text
python <skill>/scripts/mcp_workflow.py operation-calls job.json <工序id> --binding binding.json --capabilities capabilities.json --tools tools.json
```

helper只给出创建和回读调用。检查created_and_saved后，记录返回的真实operation_index和operation.id（binding.native_operation_id），再刷新快照；不要把计划中第几道工序当作原生index。发生超时先get_studio_operations/inspect_studio_project确认是否已创建，不能盲目重建。

kind=pocket映射为create_2d_pocket，代表平面区域清理，不是三维型腔或完整面铣。创建时起始深度明确为0；job工序可提供start_depth_mm（默认0），depth_mm表示相对同一参考面的结束深度。若已保存区间与计划不同，helper先输出depth_only阶段调用set_studio_operation_depths并回读，刷新快照后再继续选刀配置；不得把深度调整和生成混成不检查的批处理。改深度后旧计算结果作废。改变这个值不会重新测量厚度，也不会改变机床原点。

配置阶段binding增加operation_index及tool：

```json
{"group":"已存在的任务专用组","name":"本任务刀具名","material_profile":"已核对的材料标签","mode":"register"}
```

mode可选register或select_existing。register会增改指定组内同名刀具，因此须明确这就是本任务可修改的条目；没有专用组且不能创建时报告缺口，不覆盖不相关刀具。select_existing不修改刀库，但必须检查返回参数与计划一致。

字段映射：diameter_mm→diameter，shank_mm→shank_diameter，flute_length_mm→flute_length；rpm→spindle_speed，feed_mm_min→feedrate，plunge_mm_min→plunge_feedrate，stepdown_mm→step_down，step_over_mm→step_over_mm。原生注册接口要求全部五项速率/策略数值，即使钻孔也不能省略或伪造横向步距；从实际计划/已确认刀库读取，不确定时说明接口缺项。该字段不证明实际啄钻退屑动作。

当前只验证了平底铣刀和钻头的录入，刃数、钻尖角、刀肩/伸出等不能假称已经写入。create调用仍限于毛坯厚度和10mm。v0.5后续深度编辑可显式传through_cut_allowance_mm（0～2mm），结束深度不超过毛坯厚度加许可量且不超过10mm；未知垫板许可量不能默填。

配置输出分成calls与after_verification：先执行注册/选刀，逐条检查verified，再把select返回expected_saved_tool_after_generation与本job的参数比较；一致后才能调用generate。生成后验证新运动网格、保存参数、钻孔退屑及源模型哈希。helper拒绝旧revision、过期快照、错误几何/index、缺少步距及当前连接缺失的工具；这仍是记录一致性检查，原生适配器会再次校验实际应用状态。


工序可提供tabs={count,width_mm,height_mm,shape}。helper在选刀检查后的after_verification中先输出configure_studio_contour_tabs，再输出generate；这不是无需检查的批处理。详细页提交后须回读，并根据返回要求重新选刀、从列表重算，核对保存的刀具参数。原生支撑位置与留存运动另行检查。through_cut_allowance_mm按工序传递，先创建毛坯内占位区间，刷新快照后再编辑至允许区间；不能用全局名义余量替代实际依据。


v0.8的kind=relief_3d映射为create_3d_relief：使用已验证模型mesh UUID、新鲜SHA和明确relief.layered/allowance_mm，参数改变使用configure_studio_3d_relief检查点，不调用二维深度编辑器。此时depth_mm是计划深度预算，不是写入二维字段；边界及具体记录见[十四种工序](operation-types.md)。其余特征/孔/边、旋转和激光工序仍不能由helper生成自动调用。

v0.8.2的kind=chamfer使用SVG边及现有90°V刀。工具填kind=engraver、geometry=v、included_angle_deg=90及tip_diameter_mm；工序chamfer填depth_mm、tip_offset_mm、side和direction。工序外层depth_mm须等于start_depth_mm+chamfer.depth_mm，后者才是传入chamfer_depth_mm的倒角量。刀尖实际深度还须加端部偏移，且不能超出毛坯、有效刃长与锥刃限制。

创建、选刀核对、暂存倒角参数与计算分别验收，不调用通用set_studio_operation_depths修改倒角。返回chamfer_saved_but_tool_parameters_need_reselection时保留已保存工序，重新选刀、从列表重算，刷新快照并核对全部参数；不盲目重建工序。二维倒角不代表三维边倒角已接通。
