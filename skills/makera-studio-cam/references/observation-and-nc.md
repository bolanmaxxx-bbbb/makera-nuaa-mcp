# 视频、事件证据与NC文本

这些能力按用户任务需要调用，不把视频接入作为CAM前置条件。保存于本地的图片、ZIP、设备地址、NC及任务记录不自动上传或进入Git；尤其遵守用户排除的现场资料范围。

## 视频与状态组合

1. get_camera_status()只读接收器状态，不打开视频连接。先检查camera_configured/connected、最后接收时间及已知限制；没有地址时只在用户确实需要视频后补问，不能猜测地址。
2. 为本次观测调用set_task_context(task_id, operation)，上下文写入未来证据。它不会修改Studio或启动加工，也没有revision专用字段；在operation标签写明job/server修订和观察目的。
3. start_camera()订阅已配置视频，等待新接收帧；capture_frame()返回一张实际JPEG和observation元数据，必要时可以直接采样而不单独start。
4. sample_frames(count, interval_s)返回1–8张有序帧，间隔0.2–5秒，总采样窗口不超过20秒。先看清晰度、遮挡、光照、画面覆盖及接收连续性，再解释可见现象。
5. get_observation_bundle(count, interval_s)用于需要前后Studio原生状态的采样，返回studio_before/after和图像序列；不是相机与控制器的严格同步采样。若Studio状态不可读，说明限制，按需要退回纯sample_frames，不能编造设备状态。
6. stop_camera()只关闭此MCP进程自己的视频订阅，不是急停、不停止主轴或机床。已在使用且仍需采样时保持连接；为一次性观测打开的订阅在结束后关闭。

实际调用返回TextContent与ImageContent混合内容；读取文本元数据并查看图像，不能只看“有图片”就判定状态正常。服务没有可靠的源拍摄时间戳，received_at/接收年龄不等于曝光时间或控制器数据新鲜度；断连后不得用旧帧冒充当前现场。

## 事件与证据导出

list_visual_events(limit)的limit为1–100。它读取接收器/任务上下文事件，anomaly_classifier_enabled=false，不是已实现的断刀、碰撞或切削负载分类器。可以描述画面中可见变化；无法确认时标记不确定，不能据此继续机床运动。

capture_frame/sample_frames/get_observation_bundle的文本结果里有observation.id和manifest_path。需要交付本地证据时调用export_event_evidence(observation_id)，从返回值记录archive_path和bytes，并检查文件存在。不要把task_id、帧sequence或事件下标当作observation_id。保留策略可能移除旧图片；导出缺文件时说明证据已过期，不重复声称导出成功。ZIP只包含本次观测清单与图片，导出不等于上传。

## 现有NC检查

用户提供本地.nc/.gcode/.tap时，调用inspect_nc_file(path)，保留其sha256、单位/距离模式命令、G/M代码及literal_word_ranges。服务只接受不超过32 MiB的本地文本，不生成、不后处理、不发送代码。

literal_word_ranges是字面数值范围，不能当作实际行程包络；工具不重建偏置、圆弧、表达式、子程序或碰撞。v0.9.0的sequence_audit额外检查受限标准指令顺序、末尾主轴/冷却关闭与F继承，仍不验证实际停机或Z1后处理兼容性。machining_approved必须保持false。NC文本检查不能据此把技能evidence.nc_verified或simulation_verified设为true。效率与收尾验收见[走刀效率与程序收尾](path-efficiency-and-shutdown.md)。若用户要求真正后处理/仿真，报告对应能力尚未实现。
