# 场景与资产接入规范

## 当前缺口

本轮代码可以运行 Go2 MJWarp、加载低层 policy、传递 VLM 动作，并已在真实 OrcaLab 中验证 `UpdateLocalEnv`、Go2 ego camera 刚性跟随和 512×512 RGB 拉帧。当前安装的 engine build 不发送 H.264 WebSocket packet，因此默认使用 edit-gRPC PNG transport；真实相机链已能做 pipeline smoke。要复现 NaVILA-Bench episode，仍缺少至少三类相互匹配的数据：

1. Matterport 场景的 OrcaLab/3DGS 可视资产。
2. 与可视表面同坐标、同尺度的 MuJoCo collision geometry。
3. dataset/Matterport、MJWarp world、OrcaLab render world 与 camera optical frame 的标定关系。

在三者完成并通过验证以前，所有 episode 必须报告：

```text
scene_fidelity=false
```

纯 flat-ground Go2、程序化 RGB、仅有 3DGS 而无碰撞、仅有碰撞而无对应视觉，都不能设为 true。

## 建议的资产包接口

每个 `scene_id` 建议提供一个版本化 manifest。具体文件格式可选 JSON/YAML，但字段语义应固定：

```yaml
schema_version: 1
scene_id: "<NaVILA/Matterport scene id>"
units: meter
handedness: right
up_axis: z

render:
  asset_path: "<OrcaLab 3DGS/scene asset>"
  asset_revision: "<immutable revision>"

physics:
  mjcf_path: "<collision scene.xml>"
  mesh_root: "<mesh directory>"
  asset_revision: "<immutable revision>"

coordinates:
  T_orca_from_dataset: [[... 4x4 row-major ...]]
  T_mjwarp_from_dataset: [[... 4x4 row-major ...]]
  quaternion_order: wxyz

robot:
  render_asset_path: "assets/e071469a36d3c8aa/unitree_robots/prefabs/go2_usda"
  root_free_joint_qpos: [0, 7]
  joint_map: "<name-to-qpos-address artifact>"

camera:
  name: navila
  parent_body: "<Go2 camera mount body>"
  T_body_from_camera: [[... 4x4 ...]]
  width: 512
  height: 512
  color_ws_port: 7080
  depth_ws_port: 7081
  intrinsics: {fx: 0, fy: 0, cx: 0, cy: 0}
```

端口 7080/7081 只是 [OrcaManipulation](https://github.com/openverse-orca/OrcaManipulation) 中常见的 color/depth 配对示例，不是协议硬编码值。实际端口必须从 workspace/scene manifest 读取并做占用检查。

manifest 还应记录源数据 hash、转换工具版本和生成时间；不要用可变目录名推断资产版本。

## 坐标约定

核心代码统一使用：

- 右手世界坐标。
- 距离单位为米，时间单位为秒，角速度为 rad/s。
- 四元数顺序 `(w,x,y,z)`。
- `VelocityCommand` 位于 robot body frame。
- `EpisodeSpec` 的起点、目标、reference path 与 GT locations 初始属于 dataset frame。

对每个场景显式保存刚体变换：

```text
p_orca   = T_orca_from_dataset   · p_dataset
p_mjwarp = T_mjwarp_from_dataset · p_dataset
```

不能只对 position 加 offset 而忽略 yaw、axis permutation、unit scale 或 quaternion convention。若 render 与 collision 分别使用不同导出链，应验证：

```text
T_orca_from_dataset ≈ T_orca_from_mjwarp · T_mjwarp_from_dataset
```

建议至少用三个不共线 landmark 和一个已知朝向做自动标定测试。起点应用后还应检查 Go2 feet 与地面高度，防止视觉对齐但碰撞场景上下偏移。

## 3DGS 与碰撞体

3DGS 只负责视觉，不能作为 MuJoCo 接触几何。物理资产应单独提供：

- 地面、墙、门框、楼梯和大型家具的 collision mesh/primitive。
- 合理的 `contype`、`conaffinity`、`condim` 与 friction。
- 与 3DGS 相同的米制尺度、原点和朝向。
- 避免过密三角网格导致 MJWarp contact buffer 或性能异常。

接入时进行以下几何检查：

1. 从 OrcaLab compiled MJCF 导出非机器人 collision geom 清单。
2. 比较 3DGS 可见地面和 collision ground 的高度。
3. 在 reference path 上采样，检查路径点是否落入墙体或悬空。
4. 在门、楼梯和窄通道测试 Go2 footprint clearance。
5. 静置 Go2，确认无持续下沉、弹飞或 NaN contact。

`OrcaLabBatchRenderer` 可以从 downloaded combined model 枚举 geom，但该清单只能证明 OrcaLab scene 中存在 geom；仍需与用于 MJWarp 的 collision model 做 hash/transform 对照。

## Go2 actor 与 joint map

当前渲染侧默认 Go2 asset 为：

```text
assets/e071469a36d3c8aa/unitree_robots/prefabs/go2_usda
```

物理侧 `MjlabGo2Backend.joint_qpos_addr` 从 MJLab robot metadata 动态取得 `{joint_name: local_qpos_address}`，这是 joint mapping 的唯一权威来源。不要假设 XML 顺序、policy action 顺序、Isaac Lab 顺序和 OrcaLab combined MJCF 顺序相同。

现有 combined-scene 映射规则为：

1. local Go2 root free joint 必须位于 `qpos[0:7]`：xyz + `(w,x,y,z)` quaternion。
2. named hinge joint 使用 `joint_qpos_addr[name]` 取 local qpos。
3. actor 命名为 `go2_000`、`go2_001` 等。
4. combined MJCF 中 joint 名必须是 `<actor>_<local_joint_name>`。
5. root free joint 通过 actor 名或 actor 前缀发现，不能靠固定 combined index。
6. actor 初始位置来自 combined model `qpos0`，scatter 时只向 root xyz 添加已确认的 scene offset。

接入新 Go2 asset 时应生成并保存一份报告：local joint name/address、combined joint name/address、policy action index、默认 qpos 和符号方向。必须进行逐关节小角度 sweep，目视和数值确认同一条腿、同一方向响应后才能进入端到端测试。

## 相机接口

当前可运行路径使用 `OrcaGrpcPngCamera` 调用 edit service `GetCameraDataPNG`：

- `/navila_ego` 是根级 `prefabs/agentcamera` actor，RGB-only、512×512。
- 每次 `UpdateLocalEnv` 前从 Go2 `qpos[0:7]` 计算相机世界位姿。
- 拉帧 RPC 返回 `AgentCamera_color_<index>.png`，读取完整文件后转为 HWC `uint8` RGB。
- 实测相机位置误差 `1.33e-8 m`，四元数绝对点积 `0.9999999999999998`。

可选实时路径使用 OrcaGym `CameraWrapper(camera_name, color_ws_port)`：

- WebSocket 消息为时间戳 + H.264 payload。
- 使用 PyAV 解码。
- 导航请求 `get_frame(format="rgb24")`，得到 HWC RGB 和 frame sequence。
- `OrcaLabRenderBridge` 在 `UpdateLocalEnv` 返回后记录 sequence fence，只接受随后严格更大的 frame，避免把 RPC 期间到达的旧姿态帧误关联到新 qpos。

当前 `CameraWrapper` 实际只用 `color_ws_port` 连接 `ws://localhost:<port>`；`camera_name` 是本地 provenance 标签，不参与远端相机选择。生产 manifest 必须保存并在 edit service 中校验 camera actor → WebSocket port 映射，否则一个可解码的错误端口也可能被误标为 ego image。

相机必须挂在 Go2 的指定 body 上，而不是 viewport/fly camera。manifest 必须记录：

- parent body 与相机相对位姿。
- optical axis/up axis convention。
- RGB resolution，目标为 512×512。
- FOV 或完整内参 `fx/fy/cx/cy`。
- color/depth/normal 各自端口，且确认哪个端口送入 VLM。
- near/far plane、曝光及必要的畸变参数。

`GetCameraDataPNG` 是当前 engine build 的兼容路径，但其文件式返回不适合作为最终高频 hot loop。该 build 会监听 WebSocket 并启动 NVENC，但没有把编码 packet 写入 `StreamingHandler`；修复 engine streaming 后应切回 H.264 内存帧。

当前 `CameraWrapper` 连接 localhost；若 OrcaLab 位于另一台机器，需要显式端口转发，并把网络延迟计入 freshness timeout。不要因为拿到一张有效 RGB 就认为它对应当前 qpos，frame sequence 检查不可关闭。

当前本机除 level camera 外已创建 `/navila_ego`。它不是 level 截图：camera world transform 由 Go2 base pose 与固定外参逐步合成，`GetCameraDataPNG("AgentCamera", ...)` 返回的也是该 actor 的前向画面。

## OrcaLab 发布与启动顺序

建议顺序：

1. 激活固定的 `orcalab` 环境。
2. 初始化并检查 workspace `.orcalab/config.toml`。
3. 配置 external simulator、scene asset、Go2 actor 和 camera monitor 端口。
4. 启动 OrcaLab GUI，等待 workspace 加载完成。
5. 确认 OrcaGym/edit gRPC 可连接；若选择 `websocket`，还需确认实际收到 H.264 packet。
6. 发布单个 Go2 actor；等待 OrcaLab 编译 combined MJCF。
7. 调用 `load_model_xml()` 并建立 joint map。
8. 推送静态 qpos，先独立确认 `UpdateLocalEnv` 成功。
9. 独立确认 ego camera transform 跟随姿态且 RGB 为新抓取；WebSocket 模式还需确认 sequence 增大。
10. 再启动真实 MJWarp policy 和 VLM episode。

发布会清空/重建远端 scene；在共享 workspace 中运行前要明确操作范围。`grpc.aio` channel 和 `OrcaGymScene` 必须绑定同一个 asyncio event loop，否则会出现 “Future attached to a different loop”。

## 分阶段验收

### A. Asset schema

- manifest 字段完整，路径和 revision 可解析。
- 两个 transform 可逆、finite，旋转部分接近正交矩阵。
- joint 和 camera port 无重复/缺失。

### B. Static scene

- OrcaLab 能加载 3DGS/scene。
- MJWarp 能加载 collision MJCF。
- 三个以上 landmark 在 render/physics/dataset 中对齐。

### C. Robot mapping

- Go2 free joint 与 12 个 hinge joint 映射完整。
- reset pose、足端高度和关节方向正确。
- 100 个 qpos update 无 RPC 错误或非递增时间。

当前进度：单个 Go2 的 19 个 local qpos 已映射到 combined `nq=26`，12 次真实 update 已通过；100 次稳定性测试仍是正式资产验收目标。

### D. Camera

- 第一帧在 timeout 内到达。
- RGB 为 `uint8[512,512,3]`。
- sequence 单调递增，机器人运动能在图像中对应出现。
- 八帧 JPEG/base64 请求可被原 NaVILA server 解码。

当前进度：真实 ego camera 已达到 512×512、前向姿态和 Go2 transform 跟随；`grpc-png` 首帧已进入 runner。WebSocket sequence 项等待 engine 恢复 packet streaming。

### E. Scene fidelity

只有同时满足以下条件才把 `scene_fidelity` 改为 true：

- episode `scene_id` 精确映射到该资产 revision。
- start pose、goal、reference path 和 GT locations 已转换并验证。
- 3DGS、collision 与 camera extrinsics 对齐。
- Go2 reset 实际应用 episode start pose。
- 导航过程没有使用程序化画面、flat-ground 替代或缺失碰撞。

## 上游资料

- [OrcaGym](https://github.com/openverse-orca/OrcaGym)：Orca gRPC/Gymnasium 与仿真接口。
- [OrcaLab](https://github.com/openverse-orca/OrcaLab)：workspace、GUI 与资产环境。
- [OrcaManipulation](https://github.com/openverse-orca/OrcaManipulation)：camera monitor、数据采集和 color/depth 端口模式。
- [unitree_rl_mjlab](https://github.com/unitreerobotics/unitree_rl_mjlab)：Go2 task、asset、RSL-RL config 和 checkpoint 运行方式。
- [mjlab](https://github.com/mujocolab/mjlab)：manager-based API 与 MJWarp backend。
- [UniLab](https://github.com/unilabsim/UniLab)：未来多进程 backend adapter/rollout IPC 的参考，不是当前物理后端。
