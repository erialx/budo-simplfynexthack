# 架构与时序

## 设计目标

本项目让 NaVILA 的高层视觉语言导航逻辑不再依赖 Isaac Lab，同时保留原始八帧输入和动作语义。物理、低层 locomotion policy、渲染、VLM 传输与评测通过小接口连接，外部仿真器不能把自己的数据布局泄漏到核心导航循环。

```text
VLN episode / instruction
            │
            ▼
     NavigationRunner
       │            │
       │            └── 8-frame TCP ──► NaVILA VLM server :54321
       │                                  │ text action
       │                                  ▼
       │                         strict action parser
       │                                  │ vx, vy, wz, duration
       ▼                                  ▼
MjlabGo2Backend ── 50 Hz ──► trained Go2 policy ──► MJWarp physics
       │ RobotState + qpos_batch
       ▼
 RenderBridge
   ├── GrpcRenderBridge ──► procedural smoke server :50061
   └── OrcaLabRenderBridge
          ├── UpdateLocalEnv ──► OrcaGym gRPC
          ├── ego pose ────────► OrcaLab edit gRPC
          └── 512 RGB ◄──────── edit-gRPC PNG / H.264 WebSocket
```

这里存在三条不同的网络链路，不能混称：

1. NaVILA VLM 使用自定义 TCP 长度前缀 JSON，默认端口 54321。
2. `GrpcRenderBridge` 是本仓库的诊断 gRPC 协议，默认示例端口 50061，不是 OrcaGym RPC。
3. `OrcaLabRenderBridge` 内部调用 OrcaGym `UpdateLocalEnv`；当前本机通过 edit gRPC 拉 ego RGB，修复过 packet streaming 的 build 也可选择 camera WebSocket H.264。

## 接口边界

### Engine-neutral contracts

`contracts.py` 是边界定义：

- `EpisodeSpec`：episode、instruction、起点、目标、reference path 和 GT locations。
- `RobotState`：一个控制 tick 的同步状态。
- `VelocityCommand`：body frame 的 `vx/vy/wz` 与精确模拟持续时间。
- `RenderFrame`：`uint8[H,W,3]` RGB，并携带 `step_id`、`sim_time_s` 与 frame token。
- `VelocityPhysicsBackend`：后端内部同时拥有低层 policy 和 physics；这是当前 Go2 首选路径。
- `JointActionPhysicsBackend` + `LocomotionPolicy`：低层 policy 独立托管时的备用路径。
- `RenderBridge`：只消费状态/qpos 并返回与该状态绑定的新帧；runner 优先使用拆分的 `push_state()`/`capture()`，并兼容只实现同步 `render()` 的旧 bridge。
- `VLMClient`：只接受恰好八张 PIL RGB 图和 instruction。

公共位姿采用右手世界坐标，四元数顺序固定为 `(w,x,y,z)`。各 simulator/asset adapter 必须在边界处完成转换。

### 物理与低层策略

`MjlabGo2Backend` 从本地 [unitree_rl_mjlab](https://github.com/unitreerobotics/unitree_rl_mjlab) 注册 `Unitree-Go2-Flat`，构造 `ManagerBasedRlEnv` 和 `RslRlVecEnvWrapper`，再加载真实 RSL-RL checkpoint。VLM 的速度命令写入 task 的 `twist` command term，并阻止 command manager 在动作持续期间重新采样。

物理状态的权威来源始终是 MJWarp。OrcaLab 不参与接触求解，也不把渲染世界的状态写回 policy。这样可以避免 OrcaGym 的单 world CPU 同步路径进入 GPU policy hot loop。

当前 `/home/user/OrcaLab-RSLRL` native registry 没有 Go2 training task。Go2 task、policy 与 physics 来自 unitree_rl_mjlab；现有 OrcaLab-RSLRL training smoke 只验证通用 RSL-RL plumbing。

上游 [mjlab](https://github.com/mujocolab/mjlab) 提供 Isaac Lab 风格的 manager API 与 MJWarp backend；本项目固定使用 Unitree 项目兼容的 mjlab 1.2.0，而不是当前 main。

### 渲染

`GrpcRenderBridge` 将 `RobotState` 和可选 qpos 序列化，经本仓库 generic gRPC 服务取得 RGB。server callback 生成程序化画面，只用于 transport 和 orchestration smoke；它不模拟 OrcaLab、3DGS、碰撞、光照或 camera latency。

`OrcaLabRenderBridge` 是真实渲染路径：

1. 复用 `/home/user/OrcaLab-RSLRL/orcalab_rslrl/orcalab_batch_render.py` 的 `OrcaLabBatchRenderer`。
2. 通过 `OrcaGymScene` 发布 actor，并由 `OrcaGymLocal.load_model_xml()` 取得 OrcaLab 编译的 combined MJCF。
3. 根据 free joint 与带 actor 前缀的 named joint 建立 local qpos → combined qpos scatter map。
4. 调用 `UpdateLocalEnv(qpos, sim_time)`；OrcaLab 是被动 pose renderer。
5. 将根级 `/navila_ego` 按 `T_world_base × T_base_camera` 刚性跟随首个 Go2；原版 optical pose 经 Orca `AtomToRos` 约定转换为前向视角。
6. 默认用 `GetCameraDataPNG` 同步拉取 512×512 RGB；WebSocket 模式则在 `UpdateLocalEnv` 后建立 sequence fence，只接受严格更新的帧。

真实 OrcaLab 已在本机验证：单个 Go2 的 19 个 local qpos 映射到 combined MJCF 的 `nq=26`；ego camera 返回前向 512×512 RGB，位置最大误差 `1.33e-8 m`，最小 runner smoke 正常退出。当前 engine build 的 WebSocket 接收连接但不发送编码 packet，因此默认 `grpc-png`；H.264 adapter 保留为可选 transport。

[OrcaGym](https://github.com/openverse-orca/OrcaGym) 官方项目说明了其 Gymnasium、多后端、分布式 gRPC 与视觉观察定位；[OrcaManipulation](https://github.com/openverse-orca/OrcaManipulation) 展示了相机 monitor/data collection 的实际用法。

## 八帧 VLM 协议

该实现保留 NaVILA-Bench 的时间历史语义，不是八方向全景。

### 历史采样

- 每 0.5 秒模拟时间采集一帧。
- 历史少于 8 帧时，在左侧补与当前尺寸相同的纯黑 RGB 图。
- 历史达到或超过 8 帧时，前七帧使用
  `floor(i * (N - 1) / 7), i=0..6` 从完整历史均匀取样。
- 第八帧始终是最新帧。
- 输入统一转成 PIL RGB；float array 最大值不超过 1 时先乘 255，之后裁剪为 uint8。
- 每帧用 Pillow JPEG 编码后 base64；当前实现沿用 Pillow 默认 JPEG 参数。

### TCP wire format

请求 JSON 为：

```json
{
  "images": ["<jpeg-base64-0>", "... exactly 8 ..."],
  "query": "<navigation instruction>"
}
```

发送顺序：

1. UTF-8 编码 JSON。
2. 发送 8 byte unsigned big-endian payload 长度。
3. 发送完整 payload。
4. 接收端同样先读满 8 byte 长度，再精确读取对应字节数。
5. response payload 必须是 UTF-8 JSON，解码结果必须为非空字符串。

默认 VLM 地址是 `localhost:54321`；library client 默认超时 120 秒，navigation CLI 默认 180 秒，响应上限 1 MiB。客户端使用 `recv_exact` 处理 TCP 分片，不能假设一次 `recv` 得到完整 header 或 body。

动作 parser 只接受一个 canonical 动作：

| VLM 动作 | `vx` | `wz` | 持续时间 |
| --- | ---: | ---: | ---: |
| move forward 25 cm | 0.5 m/s | 0 | 0.5 s |
| move forward 50 cm | 0.5 m/s | 0 | 1.0 s |
| move forward 75 cm | 0.5 m/s | 0 | 1.5 s |
| turn left 15/30/45 degrees | 0 | +π/6 rad/s | 0.5/1.0/1.5 s |
| turn right 15/30/45 degrees | 0 | -π/6 rad/s | 0.5/1.0/1.5 s |
| stop | 0 | 0 | 0 s |

无动作、多个动作或不支持的距离/角度会报错，不会静默变成 forward。

## 准确的 50 Hz 时序

Unitree Go2 task 当前配置：

```text
MuJoCo physics timestep = 0.005 s  (200 Hz)
decimation              = 4
policy/control_dt       = 0.020 s  (50 Hz)
image_interval          = 0.500 s  (每 25 个 policy tick)
state_stream_interval   = 0.040 s  (每 2 个 policy tick，默认 25 Hz)
```

因此 0.5/1.0/1.5 秒动作严格对应 25/50/75 个 policy tick。`duration_to_ticks` 要求持续时间能整除 `control_dt`，避免 wall-clock sleep 或累计浮点误差改变动作距离。

当前 `NavigationRunner` 的顺序为：初始 reset 后推送状态并取一帧 → VLM decision → 在 50 Hz policy loop 中执行完整动作 → 默认每 2 tick 调用 `push_state()`（25 Hz）→ 每 25 tick 调用 `capture()`（2 Hz）→ 下一次 VLM decision。capture tick 会先强制推送最新姿态。所有时长都按模拟时间计算，VLM 网络耗时不计入动作持续时间。

拆分接口为：

```text
push_state(state, qpos)     # 默认 25 Hz，可配置为其他可整除 control_dt 的频率
capture(after_step_id)      # 每 0.5 s，等待对应姿态之后的新 frame
```

只实现 `render()` 的 legacy bridge 仍会在 capture tick 同步渲染。MJWarp/Go2 policy 固定为 50 Hz；默认 pose stream 为 25 Hz，不应写成 50 Hz。Orca camera 返回帧必须通过 sequence freshness 检查。

## Episode、坐标与 metrics

episode loader 不导入 Isaac Lab，直接读取 NaVILA-Bench 的 gzip JSON。数组仍位于 dataset/Matterport world frame。当前 `MjlabGo2Backend.reset()` 接受 `EpisodeSpec` 但尚不应用其 scene 和起始 pose，因为对应的 collision/3DGS/坐标转换还不存在。

`NavigationMetrics` 会计算 path length、distance to goal、Success、SPL、oracle navigation error 与 oracle success，同时输出 `scene_fidelity`。规则是：

- 只有视觉、碰撞、起点、目标和坐标都与 episode 对齐时，`scene_fidelity=true`。
- 任一资产缺失、flat ground 替代、程序化 RGB、未标定坐标或未应用 episode start pose 时，必须为 `false`。
- `false` 状态下指标只表示程序执行和数学计算成功，不可与 NaVILA/Isaac 原结果比较。

## 生命周期与错误边界

- 重型 MJLab/Orca 依赖均延迟加载，使 CPU contract 环境可以 import 核心包。
- backend、renderer 和 camera 都必须显式 `close()`；推荐使用 context manager。
- physics `step_id` 必须严格递增。
- renderer 返回帧的 `step_id` 必须等于请求状态；gRPC frame token 和 Orca camera sequence 必须严格递增。
- qpos、状态、policy action 与 RGB shape/dtype 都会在边界检查，NaN/Inf 立即失败。
- gRPC、camera、VLM 三种超时分别报告，避免把外部服务失联误判成 policy 错误。

## 可借鉴但未耦合的上游

[UniLab](https://github.com/unilabsim/UniLab) 的 backend adapter 和共享内存 rollout ring 对未来多进程采样有参考价值；当前导航为单/少量环境且 VLM latency 占主导，没有引入 rollout ring。UniLab 当前 main 也不是本项目的 MJWarp 物理实现来源。
