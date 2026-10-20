# 当前MCP连接与结果契约

## 接入方式

优先使用当前聊天实际提供的`makera-studio`工具（工具命名空间可能显示为`makera_studio`）。先调用`get_capabilities`。不要只读安装目录版本号，就推断当前连接具备新能力。

服务源项目为当前仓库。STDIO命令使用实际克隆目录下的`.venv/Scripts/python.exe -m makera_mcp.server`，以当前Codex MCP配置为准，不假定开发者的目录、设备地址或环境。

完整目录验证版本0.4.0；同时检查所需工具和能力字段。旧进程需重连，不能退回Computer Use。不要自动改动其他MCP服务器、登录信息或全局安全设置。

版本号不是所有只读动作的统一门槛；按当前工具与能力的交集降级使用，报告缺项。任务工具见task-and-payloads.md，观测/事件/NC见observation-and-nc.md；完整31工具路由保存在assets/mcp-tool-map.json。

## 已验证能力与边界

| 动作 | 工具/能力 | 验收 |
|---|---|---|
| 打开隔离MKS | open_studio_project | opened=true、无未处理提示、目标项目匹配 |
| 设置名义毛坯 | set_stock_dimensions | 原生回读及保存文件一致；不冒充实测 |
| 注册刀具与参数 | register_studio_tool | 原生尺寸/材料参数表逐字段回读；优先使用已存在的项目专用组；当前未适配新建组，缺组时说明能力缺口，不覆盖其他任务条目 |
| 选刀 | select_studio_tool | 编辑器匹配；工具参数在生成时提交，尚不代表已保存 |
| 创建轮廓/钻孔 | create_2d_contour / create_2d_drilling | 几何UUID、区域尺寸、切深、工序类型匹配 |
| 创建平面区域清理 | create_2d_pocket | 原生type=0；起始0、指定结束深度、SVG选区及默认平底刀需回读；不能冒充完整面铣定厚 |
| 修改平面起止深度 | set_studio_operation_depths | 支持type0/1/2，0≤start<end≤min(10,毛坯厚度)，保存回读后必须重新计算 |
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
