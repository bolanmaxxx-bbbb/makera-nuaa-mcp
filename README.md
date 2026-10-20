# makera-nuaa-mcp

让 Codex 通过 MCP 直接调用 Makera Studio，逐步规划、生成并核对 CAM 结果。附带可复用的 `makera-studio-cam` 技能，支持引导推荐、可修改模板和自定义工艺流程。摄像头采样是辅助观测能力。

当前版本 **0.3.1**，处于开发验证阶段。采用 Windows 上的非官方原生 Qt/Frida 适配器，不依赖 Computer Use、截图、可访问性树或鼠标键盘模拟；不是 Makera 官方 API/SDK。

```text
Codex → STDIO MCP → Python / Frida RPC → Studio Qt 主线程
                                      → 项目、工序、选刀、刀路计算、保存与回读
```

## 当前能力

| 功能 | 状态 |
|---|---|
| 连接 Studio、核对程序指纹 | 已验证 |
| 打开隔离 MKS、设置毛坯尺寸、保存回读 | 已验证 |
| 注册刀具尺寸/材料参数、编辑器选刀 | 已验证；选刀参数在生成刀路时提交 |
| 创建 2D 轮廓和钻孔工序 | 已验证 |
| 原生计算、实际参数及运动顶点回读 | 已验证；钻孔另检查逐次退屑序列 |
| 任务记录、输入检查和修订管理 | 已实现 |
| 摄像头状态、JPEG 采样及本地证据导出 | 辅助功能，需配置已验证的本地视频地址 |
| 面铣、型腔、曲面等其余工序 | 尚未完成适配 |
| 仿真结果验证、NC 后处理交付 | 尚未实现 |
| 机床探测、移动和切削控制 | 不提供 |

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

技能位于 `skills/makera-studio-cam/`。将整个文件夹安装到实际的 Codex skills 目录（默认是用户目录下的 `.codex/skills/`）；已有同名技能时先比较版本。

可显式调用 `$makera-studio-cam`，也可由 Makera 加工需求匹配。入口包括：

- **引导推荐**：补齐目标和限制，比较少量可选方案。
- **选择模板**：从雕刻、孔、型腔、板件、三维表面、多装夹等起点修改。
- **自定义流程**：沿用使用者的工序，检查目标覆盖、依赖、定位及交接验收。

模板可以增删、重排、拆分工序；测厚/定厚仅为按需选择的模块。工艺菜单是规划选项，不表示对应 MCP 操作都已实现。

技能附带只读校验器：

```powershell
& .\.venv\Scripts\python.exe .\skills\makera-studio-cam\scripts\check_job.py job.json --stage plan
& .\.venv\Scripts\python.exe .\skills\makera-studio-cam\scripts\check_job.py job.json --stage cam --capabilities capabilities.json
```

从 `assets/job-template.json` 建立任务记录，字段及阶段含义见技能引用文档。校验器检查记录一致性，尚未嵌入底层 MCP 强制拦截，也不代替几何检查、仿真或现场测量。

## 软件验证流程

当前适配器只允许修改仓库 `cases/` 下的隔离 `.mks` 项目副本。自行准备测试项目，保存原文件，不覆盖源模型。

典型顺序：`get_capabilities` → `connect_studio` → `open_studio_project` → `get_studio_operations` → 创建工序 → 注册/选择刀具 → `generate_studio_toolpath` → 保存回读。

“已排队”“选刀成功”或单个状态标志不代表刀路已完成；必须检查实际保存参数和新生成的运动路径。钻孔的辅助孔形网格不等于运动轨迹。刀路显示数据也不等同于仿真或 NC 后处理结果。

公开仓库不包含现场案例、测量记录、加工项目、设备地址、摄像头采样或运行日志。`cases/`、`runtime/`、本地配置及案例专用脚本均被忽略。

## 测试

离线测试无需启动 Studio 或连接设备：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

原生联调脚本位于 `scripts/`，可能切换或修改隔离的 Studio 项目。先阅读脚本和当前应用状态，只在明确的软件验证范围运行。项目的验证结论与限制见 [VALIDATION.md](VALIDATION.md)。

接口参考：[Qt QMetaObject](https://doc.qt.io/archives/qt-5.15/qmetaobject.html)、[Frida JavaScript API](https://frida.re/docs/javascript-api/)。
