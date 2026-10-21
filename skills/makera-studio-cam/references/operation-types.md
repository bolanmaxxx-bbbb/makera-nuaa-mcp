# 十四种原生工序

用户询问所有工序、选择策略或需要三维CAM时读本页。get_studio_operation_catalog读取当前选择器，返回类别、实际type、几何/刀具要求、参数组、验证案例和执行边界。v0.8已在隔离副本逐项创建并保存全部14种工序；**创建空工序不等于已实现该工序的计算**。目录的native_creation_saved_verified、mcp_creation_tool与mcp_calculation_supported分开判断。未知新增标签只报告，不猜类型或调用槽。

| 类别 | 原生工序 / type | 选择对象与关键检查 | 专属验证案例 | MCP计算 |
|---|---|---|---|---|
| 二维铣削 | 区域清理 / 0 | 闭合矢量区域；岛、内角、层深、行距与余量 | 带岛型腔 | 已接通 |
| 二维铣削 | 轮廓 / 1 | 矢量边界；内/外/在线、补偿、进退刀、真实保留 | 内外轮廓与连接筋 | 已接通 |
| 二维铣削 | 钻孔 / 2 | 矢量孔位；刀径、通盲、钻尖、啄钻与背面许可 | 孔阵列 | 已接通 |
| 二维铣削 | 螺纹 / 9 | 矢量位置；螺纹铣刀、大径、螺距、内外/左右旋、底孔和有效牙深 | 内外螺纹样件 | 待适配 |
| 二维铣削 | 倒角 / 13 | 矢量边；V刀角度/尖端、内外侧、尺寸与尖端偏移 | 矩形/圆边内外倒角 | 已接通90°V刀范围 |
| 三维加工 | 浮雕 / 6 | 整体模型网格；摆放、分层/表面、深度余量、边界与行距 | 已验证闭合斜面与连续曲面 | 已接通受限范围 |
| 三维加工 | 特征加工 / 7 | 模型面/特征；轴向/径向余量、粗精策略、残料 | 台阶型腔与岛 | 待适配 |
| 三维加工 | 轮廓 / 8 | 模型或提取轮廓；两种引用区分、起止/结束偏移、内外侧、保留 | 台阶模型剪影 | 待适配 |
| 三维加工 | 钻孔 / 10 | 模型孔特征；孔轴、直径、深度、钻尖与退屑 | 带通盲孔的模型 | 待适配 |
| 三维加工 | 倒角 / 14 | 模型边/轮廓；已有倒角状态、刃形、尺寸/间隙 | 模型棱边样件 | 待适配 |
| 三维加工 | 螺纹 / 12 | 模型螺纹孔/特征；轴线、大径/螺距、左右旋、底孔和牙深 | 模型螺纹孔 | 待适配 |
| 旋转加工 | 旋转雕刻 / 11 | 旋转装夹的模型；旋转轴/零点、毛坯、尾顶包络、方向与余量 | 带起伏的旋转体 | 待适配 |
| 激光 | 图片 / 3 | 位图；尺度、功率/速度、行距/DPI、扫描方向、焦距与次数 | 灰度渐变图 | 待适配 |
| 激光 | 矢量 / 4 | 矢量；线条/填充/偏移填充、功率/速度、行距、焦距与次数 | 线条与填充对照 | 待适配 |

type来自当前兼容指纹下的Studio，不是跨版本协议。三维钻孔或螺纹不自动意味着联动四轴；旋转雕刻另查旋转坐标与夹具。激光目录不证明已安装激光模块或具有设备执行能力。不能用浮雕替代孔/螺纹，不能拿整体模型UUID代替未解析的面、边或孔特征。

## 每种工序独立闭环

分别核对：适用输入 → 实际对象/特征引用 → 刀具真实几何 → 专属参数和前置工序 → 原生计算 → 保存回读新运动 → 重开核对 → 同修订仿真/NC → 现场验收。三维特征需要稳定面/边/孔标识及源模型修订映射；旋转案例加全周包络和尾顶检查；激光案例加图像/矢量尺度及填充覆盖。表中待适配案例是后续验证设计，不能写成已通过。

新接口分别覆盖正例、错几何/错刀、边界和余量、旧SHA、超时/重复、修改后旧证据失效。不从参数组名称合成不存在的MCP工具；完整任务缺少某工序时保留缺口，不自行换成二维或人工操作。

## v0.8浮雕实际调用

1. 打开隔离MKS，导入毫米STL，回读单模型/单网格、变换矩阵与实际包围盒；按需要摆放。不能默认每个导入模型都需相同的90°旋转。
先完成最终毛坯配置再确定模型Z；调整毛坯后重新回读摆放，不沿用旧中心。

2. 当前仅方形毛坯、厚度≤10 mm、变换后模型完整在毛坯内。取新鲜项目SHA后调用create_3d_relief(model_uuid, layered, allowance_mm, project_sha256)。layered明确传bool，深度余量为已选定的0–2 mm值；成功须created_and_saved=true。
3. 深度来自所选模型表面。原生旧二维起止深度字段可能含无意义默认值，不据此推导三维切深。当前未开放任意最大切深、其他边界/策略/角度、多模型或多刀列表。
4. 注册/选择经核对的平底刀。球刀/圆鼻刀注册与选择尚未验证；平底刀表面加工不代替球刀表面质量验证。有效刃长需覆盖本接口的毛坯深度范围，五项参数明确，步距≤刀径、层深<刃长。回归参数不作为材料切削推荐。
5. generate_studio_toolpath后检查新gcode_polyline、完整选刀参数、模型UUID及relief_settings，再重开核对。保存数组finishing的单值是layered反值，不按英文名猜。分层粗加工和不分层表面加工可分成两道，但不强制每个任务都做两道。
6. 修改用configure_studio_3d_relief(index, layered, allowance_mm, fresh_sha)，保存后旧计算失效，再核对选刀、生成和新缓冲。超时先查现状，不盲目再建工序。

调用辅助器支持kind=relief_3d，binding.geometry_uuids仅含实际模型mesh UUID；工序声明relief.layered及relief.allowance_mm。该辅助器中的depth_mm是可达性检查的计划最大深度预算，须覆盖模型最低点，不发送为二维切深；start_depth_mm须0且无through_cut_allowance_mm。其余截深需求报告为未支持。仍按创建、配置、选刀核对、生成分阶段执行。

## v0.8.2二维倒角实际调用

1. create_2d_chamfer(geometry_uuids, chamfer_depth_mm)创建SVG边倒角，初始start=0；选定后续实际90°V刀，核对直径、刃长、柄径、角度、尖端和五项速率/步距。当前只选择现有V刀，不注册V刀或支持其他角度。信息表不显示尖端直径，适配器会只读原生刀具编辑器后取消编辑，并在生成后的MKS再次核对，不能从刀名补值。
2. configure_studio_2d_chamfer(index, start_depth_mm, chamfer_depth_mm, tip_offset_mm, side, direction, fresh_sha)暂存专用参数。side=inside/outside，direction=climb/conventional。参数回读verified只证明编辑器值，saved_verified仍为false；随后generate_studio_toolpath才提交计算和保存。
3. 原生depth.endDepth在此工序表示**倒角量**；实际刀尖深度=start_depth_mm+chamfer_depth_mm+tip_offset_mm。非零start时不能使用普通工序的start<end或绝对结束Z解释。现有set_studio_operation_depths不接受type13；必须调用倒角专用配置。总刀尖深度不得超过10mm、毛坯或有效刃长；倒角量加端部偏移还须在有效锥刃内。当前不开放切入垫板余量。
4. 详情计算可能把刀具stepOverMM写成零或无意义极小值。适配器保存预期刀具，即使只是修改已保存工序也会逐字段比较；返回chamfer_saved_but_tool_parameters_need_reselection时，先核对chamfer_settings_saved_verified，再重新select_studio_tool、从列表generate并检查完整保存参数。其他失败先检查现状，不自动新建重复工序。
5. 原生保存的contourPosition是inside=1/outside=0，workDirection是climb=1/conventional=0；由原生单选项点击及实际刀路核对，不按枚举名字猜。保存重开后同时核对几何UUID、倒角量、方向、刀具和新运动。

skill工序kind=chamfer只表示二维SVG边。工具记录kind=engraver、geometry=v、included_angle_deg=90、tip_diameter_mm；工序chamfer记录depth_mm、tip_offset_mm、side和direction。通用工序depth_mm是倒角下边界的绝对深度预算，须等于start_depth_mm+chamfer.depth_mm；helper仅把chamfer.depth_mm传为原生倒角量。先选刀核对，再暂存、计算、保存回读；三维边倒角不能借用此接口。

validate_2d_chamfer_mcp.py通过真实STDIO MCP验证矩形/圆边、内外侧四组，以及非零起始深度、端部偏移、顺逆铣修订、旧SHA拒绝、新缓冲与保存重开。最终刀尖Z和解析边偏移均有采样证据，不能据此宣称完整去除/碰撞仿真、成品公差或NC已验收。

## 已验证回归的证据范围

validate_operation_inventory.py只复验14种空工序的原生type与保存。validate_3d_relief_mcp.py自行生成闭合斜面和连续曲面，通过真实STDIO MCP完成分层粗加工、表面加工、保存与重开，检查模型实际高度、内部XYZ变化段，并将采样刀尖点与已知解析面及刀具半径/曲面梯度的高度上界比较；只检查没有向下过切不能排除整体抬高或未切到表面。现场工程与报告留在cases，不进公开仓库。

解析面采样不等于完整刀具扫掠、残料、碰撞、夹具或后处理验证，不能标为simulation_verified或machining_ready。全14项都有创建证据不表示全14项都已有MCP计算接口。
