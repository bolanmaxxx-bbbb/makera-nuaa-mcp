# 当前MCP连接与结果契约

## 接入方式

优先使用当前聊天实际提供的`makera-studio`工具（工具命名空间可能显示为`makera_studio`）。先调用`get_capabilities`。不要只读安装目录版本号，就推断当前连接具备新能力。

服务源项目为当前仓库。STDIO命令使用实际克隆目录下的`.venv/Scripts/python.exe -m makera_mcp.server`，以当前Codex MCP配置为准，不假定开发者的目录、设备地址或环境。

完整目录验证版本0.8.3；同时检查所需工具和能力字段。旧进程需重连，不能退回Computer Use。不要自动改动其他MCP服务器、登录信息或全局安全设置。

版本号不是所有只读动作的统一门槛；按当前工具与能力的交集降级使用，报告缺项。任务工具见task-and-payloads.md，观测/事件/NC见observation-and-nc.md；完整42工具路由保存在assets/mcp-tool-map.json。

## 已验证能力与边界

| 动作 | 工具/能力 | 验收 |
|---|---|---|
| 打开隔离MKS | open_studio_project | opened=true、无未处理提示、目标项目匹配 |
| 设置名义毛坯 | set_stock_dimensions | 原生回读及保存文件一致；不冒充实测 |
| 导入STL/SVG | import_studio_model | 新几何UUID、源哈希及保存后的顶点尺寸；不等于刀路、摆放或机床原点已验收 |
| 单模型摆放 | set_studio_model_placement | 一个模型/一个网格、方形毛坯、完整中心及新鲜SHA；可选单轴90°倍数相对旋转，保存矩阵/中心和无关输入核对后再重开检查 |
| 读取材料/规格选项 | get_studio_stock_options | 真实分类与当前分类的子选项，不猜牌号 |
| 配置形状/材料/完整尺寸 | configure_studio_stock | verified、保存后的stock及失败回滚证据；修改后重算受影响工序 |
| 仅录入刀具几何 | register_studio_inventory_tool | 已知刀型和刃径/柄径/有效刃长，无材料参数、不选为工序刀具；保存后关闭重开，重复请求不覆盖已有工艺条目 |
| 注册刀具与参数 | register_studio_tool | 原生尺寸/材料参数表逐字段回读；优先使用已存在的项目专用组；带参数注册接口本身不创建组；可先用仅录入流程建立自己的独立组，不覆盖其他任务条目 |
| 选刀 | select_studio_tool | 编辑器匹配；工具参数在生成时提交，尚不代表已保存 |
| 创建轮廓/钻孔 | create_2d_contour / create_2d_drilling | 几何UUID、区域尺寸、切深、工序类型匹配 |
| 创建平面区域清理 | create_2d_pocket | 原生type=0；起始0、指定结束深度、SVG选区及默认平底刀需回读；不能冒充完整面铣定厚 |
| 二维倒角 | create_2d_chamfer / configure_studio_2d_chamfer | SVG边、现有90°V刀；显式起始深度、倒角量、端部偏移和内外/顺逆铣；暂存不等于保存，计算后核对刀具及chamfer_settings，再保存重开 |
| 修改平面起止深度 | set_studio_operation_depths | 支持type0/1/2，默认0≤start<end≤min(10,毛坯厚度)，显式垫板余量可扩展结束深度，保存回读后必须重新计算 |
| 生成并提交 | generate_studio_toolpath | verified=true；本修订的实际刀具参数、非空运动路径及保存文件核对 |
| 读取工序证据 | get_studio_operations | 比较live与saved，不能互相代替 |
| 只读MKS检查 | inspect_studio_project(path) | 实际文件哈希、毛坯、SVG UUID/局部顶点范围、工序及计算证据；不能证明已连接设备 |
| 当前原生状态 | get_studio_state(include_tree=false) | available、项目标题和当前属性；只有诊断需要时才取tree，读失败不伪造状态 |
| 兼容性/方法诊断 | describe_studio_cam_api | 返回objects中的原生方法签名；发现方法不代表允许直接执行未适配的槽 |
| 原生面铣定厚 | 尚未验证 | 不跳过；不能用一条外轮廓冒充 |
| 仿真验收/NC后处理/运动 | 尚未实现或未验证 | 不宣告通过，不绕过服务发送指令 |

软件当前能力只覆盖部分工作流。技能不会新增MCP原本不存在的面铣、仿真或机器控制功能。

工艺菜单覆盖的孔、型腔、曲面、翻面等是规划选项，不表示这些操作已经全部适配。允许用户选择外部/人工交接，但必须保留该选择和验收记录；不得自行把未支持的自动操作改为人工完成。

## 操作顺序

1. 检查原始模型哈希，建立任务自己的cases副本；不覆盖源模型，不触碰旧现场任务。
2. 核对任务revision、装夹setup_id、坐标参考和计划。先完成`check_job.py --stage cam`，保存当前get_capabilities结果作为能力快照。
3. 每道工序核对几何UUID及尺寸。设置/选刀后生成，等待完成，再读取保存的MKS。
4. 比较保存的刀具ID、类型、刃径、有效刃长、柄径、转速、进给、下刀速率、层深及操作深度。不能把未适配字段伪造为已写入，例如刃数当前需在任务记录中保留。
5. 检查刀路语义、工序顺序和相应输入revision。钻孔关联的辅助孔形网格不等于实际运动路径；需要啄钻时核对有序运动点。
6. 出现未知提示、超时或进程重启后先读状态，不盲目重复创建操作或刀具。不要自动丢弃用户未保存的项目。
7. 正式任务的每道必需工序都要有证据；最终长度依赖面铣/背面处理时，单独验收其贡献。现场加工后仍需实际测量最终尺寸。

## 交付证据JSON（校验脚本输入）

```json
{
  "revision": 1,
  "simulation_verified": false,
  "nc_verified": false,
  "operations": [
    {"id": "drill-1", "calculated": true, "saved_verified": true, "parameters_match": true, "motion_path_verified": true}
  ]
}
```

该示例只说明结构，不是本任务的实际证据。布尔值必须来自具体读回/检查记录，并在任务档案中保留证据文件路径。仿真不可用时保持false；若用户仅要软件项目可交付当前阶段，但不得标为正式加工已验收。


## 连接筋与板底补偿（v0.5）

`configure_studio_contour_tabs(operation_index, count, width_mm, height_mm, shape)`只支持2D轮廓、自动按数量布局；shape为rectangle或triangle。返回值只证明编辑器数值，尚未保存。随后`generate_studio_toolpath`从详细参数页计算并保存，比较tab_settings与expected_saved_tabs_after_generation。修改后的支撑点和有序刀路仍须另行核对；开关、数量或显示模型不能证明连接筋位置、留存厚度或强度正确。

刀径接近或超过零件特征尺寸时，还须按实际刀具扫掠检查连接筋附近的保留范围；设定宽度不一定等于最终残留宽度，不能仅凭参数表认定零件易于分离。

原生详细页计算可能改写工具字段。返回`requires_tool_reselection_after_detail_commit=true`或`tabs_saved_but_tool_parameters_need_reselection`时，回读后重新select_studio_tool，再从列表generate并比较实际刀径、刃长、转速、进给、层深和步距。不能把连接筋设置成功当作工具参数已验收。不对第二次重算盲目重复，先检查现存工序与结果。

`set_studio_operation_depths`新增可选`through_cut_allowance_mm`，默认0，显式值限制在0～2mm，end仍不得超过10mm或毛坯厚度加该余量。创建工序仍先在毛坯内建立，再调用此工具设置所需区间。余量是允许写入的界限，不会自动增加实际切深，也不表示现场已允许钻入垫板；使用实际钻尖几何、贯穿量和用户确认的垫板许可量建立依据。该参数不改变Z基准或触发运动。

原生数值框可能限制小数位；无法精确表达的深度或连接筋尺寸会在写入前被拒绝，不默默四舍五入。调用方须明确选择可表达的值，再重新核对贯穿量和许可上限。

## 模型导入与毛坯（v0.6）

1. 打开任务自己的隔离MKS；当前不能原生新建或另存工程。保存源MKS和导入文件的哈希，确认Studio无未处理提示。不得把历史案例复制成通用模板发布。
2. 导入使用`import_studio_model(path, units="mm", allow_duplicate=false)`，目前仅STL/SVG，文件非空且不大于64MiB。STL无内置单位，明确来源单位并先换算；SVG的mm、px、viewBox和嵌套变换也须按已知尺寸复核。导入不自动创建任何CAM工序。
3. 检查`imported`和`saved_verified`，保存返回的新UUID。STL检查`new_models_3d[].meshes[].dimensions_mm`；SVG检查`new_svg_geometry[].bounds_in_serialized_coordinates_mm`并核对目标。它们是模型/节点局部几何，不能据此声明最终布局、父变换或机床坐标正确。
4. 相同文件在本项目已有确认导入记录、且对应UUID仍存在时返回`outcome=already_present`，无需再次导入。明确要第二份时才用`allow_duplicate=true`。超时、未知提示、源文件变化或未验证结果不具有可靠去重证据；先读当前项目再决定，不能循环重试。
5. 调用`get_studio_stock_options()`读取`cmbMaterial`（分类）、`cmbMaterial_2`（当前分类下材料）和`cmbMaterial_3`（当前材料规格）。当前接口不浏览所有分类的全部子项；目标标签未提供时不要猜，报告目录查询的缺口。Studio未收录的牌号保留在任务记录中，不能自动用通用材料替代。
6. 自定义方形示例：`configure_studio_stock({"shape":"box","dimensions_mm":{"x":40,"y":30,"z":6}})`；圆棒示例：`{"shape":"cylinder","dimensions_mm":{"diameter":20,"z":8}}`。数值仅是结构示例。形状变化必须提交该形状所需的完整尺寸；只改材料时成对提交`material_category/material_name`。
7. 材料库规格使用`{"material_category":"<真实分类>","material_name":"<真实材料>","stock_specification":"<真实规格>"}`，不能混传自定义形状/尺寸。核对保存的规格名称、形状及实际尺寸，`bUsingMaterialSize`或界面标签不能单独证明尺寸已提交。目录尺寸仍是名义值。
8. 配置成功必须检查`verified`和`saved_stock`。失败且返回`rolled_back`时再检查`rollback_saved_verified`；回滚不完整时停止依赖它的写入并保留现状。毛坯或模型变化需增加任务修订号，作废受影响的刀路、仿真及NC证据，重新检查深度、覆盖和夹具净空。

材料名称不证明材料切削参数适用。源几何、软件摆放、毛坯名义值、现场实测、工件基准和机床偏置分别记录。v0.7的单模型摆放范围、实际留料及重开核对见[几何摆放与留件验收](geometry-and-retention.md)；其余未验证变换、STEP等格式及未适配三维工序不借任意Qt槽补齐。v0.8已接通的模型浮雕及其专属限制见[工序分类](operation-types.md)。其余开发项见[能力覆盖清单](capability-coverage.md)。

自动恢复或打开工程失败时，先保留可恢复的原始数据及哈希，再检查恢复副本能否实际打开和保留工序；恢复按钮排队成功不等于工程已恢复。当前MCP未暴露通用弹窗确认，未知提示不能自动丢弃。

## 仅录入刀具：照片或清单

用户只要求把现有刀具入库时，不把它扩展成材料切削参数制定。先区分刀具与夹头等配件，逐项记录标签、规格次序及来源；平底/球头/尖头等端形不清、柄径与刃径无法区分时先确认。包装上的适用材料不是刀具本体材质；柄径不能代替最大刃径。型号、角度、刃数或数量只记录能读清/已经确认的值。

已知平底刀/钻头及三项完整几何时，调用register_studio_inventory_tool(operation_index, group, specification, create_group_if_missing=false)。specification仅含kind=endmill/drill、name、diameter、shank_diameter、flute_length。需隔离工程中有可打开原生选刀器的工序作为入口；不会为该工序选刀。已有组优先；明确选择新组时可传create_group_if_missing=true，只创建当前请求识别出的新空组，异步处理它自己的重命名提示，随后重新核对名称。不会通用确认其他弹窗。

该接口不启用任何材料参数。成功需verified、saved_and_reopened为true、active_material_profile_count=0，且cutting_parameters_set和operation_tool_selected为false。已存在同名刀具只有几何相符且无材料参数时才复用，否则停止，不覆盖别的工艺条目。精度不能表达的几何在保存前拒绝。录入后核对原工程选刀/刀路未变；批量录入前保留可恢复刀库备份。

未知尺寸不填库默认值或其他刀具尺寸。信息不全的条目先保存本地待补清单（空值、来源、缺项、是否已写入Studio），照片、备份和实物信息不随公共代码发布。原生编辑器可能仍有默认肩长、伸出、尖端或刀号；这些不是物理确认值，接口unverified_fields须留作后续加工检查。只有另有材料和工艺依据、用户需要配置加工参数时，才使用register_studio_tool并按正式CAM重新验收。
