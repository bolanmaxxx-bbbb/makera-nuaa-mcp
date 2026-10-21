# makera-nuaa-mcp

让 Codex 通过 MCP 直接调用 Makera Studio，逐步规划、生成并核对 CAM 结果。附带可复用的 `makera-studio-cam` 技能，支持引导推荐、可修改模板和自定义工艺流程。摄像头采样是辅助观测能力。

当前版本 **0.7.0**，处于开发验证阶段。采用 Windows 上的非官方原生 Qt/Frida 适配器，不依赖 Computer Use、截图、可访问性树或鼠标键盘模拟；不是 Makera 官方 API/SDK。

```text
Codex → STDIO MCP → Python / Frida RPC → Studio Qt 主线程
                                      → 项目、工序、选刀、刀路计算、保存与回读
```

## 当前能力

| 功能 | 状态 |
|---|---|
| 连接 Studio、核对程序指纹 | 已验证 |
| 打开隔离 MKS、设置毛坯尺寸、保存回读 | 已验证 |
| 导入 STL/SVG 并检查保存后的几何 | 已验证；STL须明确毫米单位，检查实际顶点尺寸 |
| 单模型中心位置和单轴90°倍数旋转 | 已验证；单模型/单网格、方形毛坯内，保存矩阵及重开核对 |
| 方形/圆棒毛坯、材料分类/名称、材料库规格 | 已验证；使用真实目录标签，保存后核对尺寸及材料 |
| 注册刀具尺寸/材料参数、编辑器选刀 | 已验证；选刀参数在生成刀路时提交 |
| 创建 2D 轮廓和钻孔工序 | 已验证 |
| 创建 2D 区域清理/平面型腔 | 已验证；原生工序类型0，使用选定SVG区域 |
| 修改平面工序起止深度 | 已验证；保存回读，修改后重新计算 |
| 原生计算、实际参数及运动顶点回读 | 已验证；钻孔另检查逐次退屑序列 |
| 任务记录、输入检查和修订管理 | 已实现 |
| 摄像头状态、JPEG 采样及本地证据导出 | 辅助功能，需配置已验证的本地视频地址 |
| 完整面铣定厚、三维型腔/曲面等其余流程 | 尚未完成适配；区域清理不能直接视为完整定厚链 |
| 仿真结果验证、NC 后处理交付 | 尚未实现 |
| 机床探测、移动和切削控制 | 不提供 |

完整流程的覆盖与开发顺序见[能力清单](skills/makera-studio-cam/references/capability-coverage.md)，包括工程新建、更多格式、模型布局、刀库、工序管理、多装夹、仿真、后处理及异常恢复。未接通的功能单独标注，不因技能中有流程名称而宣称可执行。

原生桥会向正在运行的 Studio 进程加载桥接代码，不修改安装文件、不处理账户或授权。兼容性由 `native-compatibility.json` 中的可执行文件指纹限制；其他 Studio 版本需要重新验证，不能仅更换哈希就认为兼容。

## 安装与配置

环境：Windows x64、Python 3.11+、受支持版本的 Makera Studio。以下 PowerShell 命令在仓库根目录运行：

```powershell
git clone https://github.com/bolanmaxxx-bbbb/makera-nuaa-mcp.git
Set-Location makera-nuaa-mcp
py -3 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e .
```

`requirements.lock.txt` 是开发验证环境的依赖快照。日常安装以 `pyproject.toml` 为准；需要复现时再核对 Python 和平台后使用锁定版本。

注册到 Codex：

```powershell
$projectPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
codex mcp add makera-studio --env PYTHONIOENCODING=utf-8 -- $projectPython -m makera_mcp.server
```

启动/重连后先读取 `get_capabilities`，确认当前进程的版本和实际能力。修改磁盘源码不代表已挂载服务自动更新。

摄像头不是 CAM 的前置条件。需要时将 `config.example.json` 复制为 `config.local.json`，填入已验证的局域网 `ws(s)://.../ws_video` 地址。也可使用 `MAKERA_MCP_CONFIG` 或 `MAKERA_CAMERA_URL` 指定配置；地址限制见 `config.py`。

## 安装工艺向导技能

技能位于 `skills/makera-studio-cam/`。可请 `$skill-installer` 从本仓库安装此目录，或将整个文件夹放入目标 Codex 实际识别的技能目录；已有同名技能时先比较版本。目录位置以目标版本和本机配置为准，参见[Codex技能文档](https://learn.chatgpt.com/docs/build-skills)，不要直接沿用作者电脑的路径。

可显式调用 `$makera-studio-cam`，也可由 Makera 加工需求匹配。入口包括：

- **引导推荐**：补齐目标和限制，比较少量可选方案。
- **选择模板**：从雕刻、孔、型腔、板件、三维表面、多装夹等起点修改。
- **自定义流程**：沿用使用者的工序，检查目标覆盖、依赖、定位及交接验收。

第一次安装时，可以直接向本机 Codex 发送：

```text
请阅读 https://github.com/bolanmaxxx-bbbb/makera-nuaa-mcp 的README，
安装 skills/makera-studio-cam 技能及配套MCP服务。
根据我的实际环境配置路径，核对Studio版本兼容性；不要套用作者的本地路径或设备地址。
调用get_capabilities核对实际版本和工具，完成一次只读连接检查，不启动机床加工。
```

安装完成后使用：

```text
$makera-studio-cam
请通过Makera Studio MCP准备这个加工项目。
模型或尺寸：[填写或附文件]
材料与毛坯实测尺寸：[填写]
刀具、装夹和垫板：[填写]
精度及检验要求：[填写]
先检查工艺顺序、定位和零件保留分离方式，缺项每次问最关键的1～3项。
在已授权的软件范围内推进到刀路生成、保存和回读核对；说明具体能力缺口。
不使用Computer Use，工程使用简洁中文名，现场运动和切削另行授权。
```

模板可以增删、重排、拆分工序；测厚/定厚仅为按需选择的模块。工艺菜单是规划选项，不表示对应 MCP 操作都已实现。

新增[加工点局部测厚模块](skills/makera-studio-cam/references/thickness-measurement.md)：选择夹具附近裸露垫板为基准，再测加工点顶面，核对垫板高度传递、垫层、重复性及测量精度。配套`check_thickness_measurement.py`只校核已有回报并输出可交接记录，不发送G-code、不实现自动探测；“表面上方20 mm”等用户要求需先转化为经过现场核对的路线净空。

技能附带只读校验器：

```powershell
& .\.venv\Scripts\python.exe .\skills\makera-studio-cam\scripts\check_job.py job.json --stage plan
& .\.venv\Scripts\python.exe .\skills\makera-studio-cam\scripts\check_job.py job.json --stage cam --capabilities capabilities.json
```

从 `assets/job-template.json` 建立任务记录，字段及阶段含义见技能引用文档。校验器检查记录一致性，尚未嵌入底层 MCP 强制拦截，也不代替几何检查、仿真或现场测量。

技能已为当前36个MCP工具建立路由，覆盖项目/CAM、任务创建与修订、原生诊断、视频采样、事件及本地证据导出、NC文本检查。`scripts/mcp_workflow.py`提供`audit`、`task-spec`和`operation-calls`三个子命令，核对实际连接并把技能记录映射成接口参数；它只输出JSON，不自动调用工具。创建、深度调整及配置选刀分阶段，真实index和文件哈希匹配后才继续；选刀参数必须核对后再生成。详见技能的`references/task-and-payloads.md`。

## 软件验证流程

当前适配器只允许修改仓库 `cases/` 下的隔离 `.mks` 项目副本。自行准备测试项目，保存原文件，不覆盖源模型。

典型顺序：`get_capabilities` → `connect_studio` → `open_studio_project` → `get_studio_operations` → 创建工序 → 注册/选择刀具 → `generate_studio_toolpath` → 保存回读。

“已排队”“选刀成功”或单个状态标志不代表刀路已完成；必须检查实际保存参数和新生成的运动路径。钻孔的辅助孔形网格不等于运动轨迹。刀路显示数据也不等同于仿真或 NC 后处理结果。

v0.4新增`create_2d_pocket(geometry_uuids, end_depth_mm)`及`set_studio_operation_depths(operation_index, start_depth_mm, end_depth_mm)`。创建时起始深度明确提交为0；深度编辑要求`0 <= start < end <= min(毛坯厚度, 10 mm)`，适用于平面区域清理、轮廓和钻孔，不能修改工件原点或机床坐标。`start_depth_mm`与结束深度均相对于该工序参考面，改变后必须重新生成并验证新运动网格。

本版Studio的原生入口是“2D区域清理”，没有将其重新命名为已验证面铣。若用于局部表面去除，仍须确认加工区域与刀具通道的覆盖、参考面、实测厚度及目标尺寸；创建一个区域清理工序不证明定厚已经完成。

公开仓库不包含现场案例、测量记录、加工项目、设备地址、摄像头采样或运行日志。`cases/`、`runtime/`、本地配置及案例专用脚本均被忽略。

## 测试

离线测试无需启动 Studio 或连接设备：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

原生联调脚本位于 `scripts/`，可能切换或修改隔离的 Studio 项目。先阅读脚本和当前应用状态，只在明确的软件验证范围运行。项目的验证结论与限制见 [VALIDATION.md](VALIDATION.md)。

`scripts/validate_planar_mcp.py`接受使用者自己的几何MKS和SVG UUID，创建全新副本验证区域清理及深度增减。输入项目须仅含几何，当前Studio须已保存且没有提示窗；脚本拒绝覆盖已有测试文件。输出报告保留在被Git忽略的runtime目录。

接口参考：[Qt QMetaObject](https://doc.qt.io/archives/qt-5.15/qmetaobject.html)、[Frida JavaScript API](https://frida.re/docs/javascript-api/)。

### v0.6：模型导入和完整毛坯配置

新增`import_studio_model(path, units="mm", allow_duplicate=false)`，通过限定于本次导入的原生Qt文件选择器导入STL/SVG。保存后返回新几何UUID，STL尺寸来自实际序列化顶点；SVG返回局部顶点范围，仍须按目标尺寸检查单位。已确认的相同输入可识别为已存在；超时或未知提示时先检查现状，不盲目重试。STEP/STP、DXF及位图尚未验证，导入本身不生成刀路。

`get_studio_stock_options()`读取当前毛坯的真实材料分类、材料及规格选项；子选项属于当前父分类。`configure_studio_stock(specification)`支持方形`box`的`x/y/z`、圆棒`cylinder`的`diameter/z`，以及准确的`material_category/material_name`标签。选材料库规格时传`stock_specification`，不要同时传自定义形状/尺寸。保存后核对实际值；材料不存在时回滚并检查保存结果，不自动替换牌号。名义毛坯、材料库尺寸和切削参数仍需分别核对。

使用自己的仅含几何MKS运行真实MCP验证（当前Studio须已保存且无提示窗；输出目录必须不存在）：

```powershell
& .\.venv\Scripts\python.exe -X utf8 .\scripts\validate_project_setup_mcp.py --project '<仅含几何的工程.mks>' --output-directory '.\cases\项目接口验证'
```

脚本生成合成STL/SVG，验证导入尺寸、毛坯配置、无效材料回滚和重复导入识别，并检查源工程哈希不变。全部输出留在忽略目录，不执行机床运动。当前仍需已有MKS；从零新建/另存工程、多模型与通用变换尚未接通；v0.7仅支持下面列明的单模型摆放。


### v0.5：连接筋与显式超深

新增configure_studio_contour_tabs：在2D轮廓详情中设置自动支撑的数量、宽度、高度与形状，编辑器回读后通过generate_studio_toolpath计算保存。保存参数与支撑点分别报告；不宣称强度或实际留存已经验证。详情提交后须重新选刀并重算，避免原生详情页改写工具字段。

set_studio_operation_depths新增through_cut_allowance_mm（默认0，范围0～2mm）；仅显式声明时才允许结束深度超出毛坯底面，并仍限制在10mm内。必须自行核对实际钻尖、参考面和垫板许可量；此接口不会建立现场基准或发送运动。

### v0.7：单模型摆放与实际留料检查

新增`set_studio_model_placement(model_uuid, center_mm, project_sha256, world_rotation_deg=None)`。当前只允许一个3D模型且仅含一个已验证网格、已有变换无缩放/镜像且轴对齐、方形毛坯内摆放；中心完整传`x/y/z`。可选旋转是单一世界轴、90°倍数、−180°～180°的相对增量，不支持多轴一次旋转、缩放或多模型。

`inspect_studio_project`新增保存的模型矩阵、变换后尺寸/中心/包围范围。输入须带当前快照SHA256，变换后检查保存矩阵、实际中心及SVG/毛坯/工序输入未被改动。局部尺寸正确不代表方向或高度正确；模型节点/名义毛坯坐标也不是机床坐标。相对旋转超时后先检查现状，不盲目重试。

仅含几何且不含3D模型的MKS可用于生成式验证：

```powershell
& .\.venv\Scripts\python.exe -X utf8 .\scripts\validate_model_placement_mcp.py --project '<几何工程.mks>' --output-directory '.\cases\模型摆放验证'
```

脚本通过真实MCP导入合成模型、旋转、移动、拒绝旧快照请求，并重开检查保存结果。技能新增[几何摆放与留件验收](skills/makera-studio-cam/references/geometry-and-retention.md)：核对3D参考与二维驱动几何、多个高度的实际留料，以及软件回归与正式交付的区别。连接筋设定宽度不保证实际残留宽度，几何扫掠检查不代替强度或仿真验证。
