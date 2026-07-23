# NaVILA-Orca

NaVILA-Orca 是 NaVILA 导航循环到 Orca/MuJoCo Warp 技术栈的第一阶段移植。当前实现已经把 episode、动作、时序、RGB 历史、VLM TCP 协议、MJLab Go2 推理后端和 OrcaLab 渲染桥拆成独立接口；在 Matterport 3DGS、匹配碰撞体和坐标标定尚未交付时，它用于验证“VLM 动作能够经过低层 Go2 策略推进 MJWarp，并经渲染桥取得新图像”的系统链路。

当前状态必须准确理解：

- Go2 物理和低层策略来自本项目入口 `components/unitree_rl_mjlab`，实际后端是 MJLab/MuJoCo Warp。
- `components/OrcaLab-RSLRL` 的 native registry 当前只有 G1，没有 native Go2 training task。本仓库没有声称已经完成 OrcaLab-RSLRL Go2 训练任务。
- `scripts/run_training_smoke.sh` 运行的是 `orcalab_rslrl.tasks.smoke:make_train_env` 的通用一轮训练 smoke，只验证 MJWarp → RSL-RL plumbing，不训练 Go2。
- 缺少与 VLN episode 匹配的 3DGS、碰撞体或坐标变换时必须设置 `scene_fidelity=false`。此时 Success、SPL 等数值只能验证计算链路，不能作为导航质量结果。
- 已验证真实 Go2 MJWarp checkpoint、NaVILA TCP VLM、八帧图像历史、gRPC RGB bridge、动作执行和结果落盘组成的有限端到端运行。
- 已在真实 OrcaLab 中验证 Go2 发布、combined MJCF 下载、local qpos 到 combined qpos 映射、`UpdateLocalEnv`，以及 512×512 Go2 ego RGB 进入统一图像管线。

当前与原版的差距见 [对齐缺口](MISSING_PARITY.md)。更详细的边界见
[架构说明](docs/ARCHITECTURE.md)，未来场景接入约定见 [资产接入](docs/ASSET_INTEGRATION.md)。

## 固定版本

已验证环境为 `/home/user/anaconda3/envs/orcalab`：

| 组件 | 兼容版本 |
| --- | --- |
| Python | 3.12 |
| OrcaLab | 26.6.3（精确锁定） |
| OrcaGym | 26.6.3（精确锁定） |

常驻 Go2 ego camera 使用 26.6.3 的 `prefabs/mujococamera1080` 与
`GetCameraPNG`；不依赖 26.6.x 中缺失属性的 `prefabs/agentcamera`。
详见 [OrcaLab 26.6.3 MuJoCo camera](docs/ORCALAB_26_6_3_MUJOCO_CAMERA.md)。

`./scripts/start_orcalab_gui.sh` 现在会默认启动一个 scene-profile watcher。
每次 GUI 打开或切换到新场景、其 MuJoCo runtime 出现后，watcher 自动注入并回读验证
`orca-train` 的全局 option（5 ms、ImplicitFast、10/20/50 solver、重力和关闭空气阻力）。
它不会重新发布场景、修改 3DGS 资产或要求先有 Go2。对于已经手动打开的 GUI，可单独运行
`./scripts/watch_orcalab_scene_profile.sh`；结束 GUI 后按 Ctrl-C 停止 watcher。
| MuJoCo | 当前环境 3.7.0 |
| mujoco-warp | 3.5.0 |
| mjlab | 1.2.0 |
| rsl-rl-lib | 5.x，当前 5.0.1 |
| warp-lang | 当前环境 1.12.0 |

不要在该环境内直接升级到 mjlab main。当前 [mjlab](https://github.com/mujocolab/mjlab) main 的依赖链已经更新，而 [unitree_rl_mjlab 的安装配置](https://github.com/unitreerobotics/unitree_rl_mjlab/blob/main/setup.py) 固定了本项目使用的 `mjlab==1.2.0` 和 `mujoco-warp==3.5.0`。OrcaGym/OrcaLab 的版本基线见各自的 [OrcaGym pyproject](https://github.com/openverse-orca/OrcaGym/blob/main/pyproject.toml) 与 [OrcaLab pyproject](https://github.com/openverse-orca/OrcaLab/blob/main/pyproject.toml)。

## 安装

前提：NVIDIA 驱动与 CUDA 可用，用户已阅读并接受 NVIDIA Omniverse EULA，本机已有上述 `orcalab` 环境以及以下本地项目：

```text
components/NaVILA-Bench
components/unitree_rl_mjlab
components/OrcaLab-RSLRL
components/orca_rl
```

为避免 pip 重新求解并升级仿真栈，优先在已有环境中以 no-deps editable 方式安装：

```bash
cd /home/user/VLN/NaVILA-Orca
conda activate orcalab
python -m pip install --no-build-isolation --no-deps -e .
./scripts/run_training_smoke.sh --check-only
navila-orca doctor --require-gpu
```

`--check-only` 不启动 CUDA 训练，只读取目标 Python 的包元数据。出现 mismatch 时先恢复固定环境，不要就地升级 mjlab 或 mujoco-warp。

CPU 开发只需要本项目基础依赖与 pytest；Orca 路径还要求 `av`、OpenCV、Torch 和 WebSocket 支持。若当前环境缺少这些包，应按固定版本单独补齐，而不是用无约束的整体升级。

## 三档验证

### 1. CPU contract

该档不启动 GPU、VLM server 或 OrcaLab，用于验证接口、严格动作解析、八帧采样、episode、metrics、时序与协议编码：

```bash
cd /home/user/VLN/NaVILA-Orca
PYTHONPATH=src /home/user/anaconda3/envs/orcalab/bin/python -m pytest -q
```

本机全套 contract/CLI/protocol 测试已通过 `54 passed, 1 skipped`。被跳过的真实 GPU backend smoke 需要显式设置 `NAVILA_ORCA_RUN_GPU=1`：

```bash
NAVILA_ORCA_RUN_GPU=1 PYTHONPATH=src \
  /home/user/anaconda3/envs/orcalab/bin/python -m pytest -q
```

受限沙箱可能禁止 socket 测试，即使服务只绑定本机 loopback；这种情况下可先运行非 socket contract：

```bash
PYTHONPATH=src /home/user/anaconda3/envs/orcalab/bin/python -m pytest -q \
  tests/test_actions.py tests/test_episodes.py tests/test_frames.py \
  tests/test_runner.py tests/test_training.py
```

### 2. 真实 Go2 MJWarp + VLM + procedural gRPC

这一档使用真实 `MjlabGo2Backend`、真实 Go2 checkpoint、真实 NaVILA VLM TCP server，但画面来自本仓库的程序化 gRPC renderer。它验证真实物理/策略/VLM/网络编排，不验证 OrcaLab、3DGS 或场景真实性。

最小 scripted smoke 已封装为：

```bash
./scripts/run_orca_smoke.sh
```

NaVILA VLM server 在 `127.0.0.1:54321` 运行时，可执行真实 VLM 路径：

```bash
./scripts/run_orca_vla.sh
```

也可以把诊断 renderer 作为独立进程启动，再给 CLI 传 `--render-backend grpc --grpc-render-address 127.0.0.1:50061`：

```bash
cd /home/user/VLN/NaVILA-Orca
conda activate orcalab
PYTHONPATH=src python -m navila_orca.render.grpc_bridge \
  --host 127.0.0.1 --port 50061 --height 512 --width 512
```

NaVILA VLM server 仍按原项目在 `localhost:54321` 启动。Go2 默认读取：

```text
task:       Unitree-Go2-Flat
checkpoint: components/orca_rl/checkpoints/test_model_Go2_mjlab_Flat.pt
task repo:  components/unitree_rl_mjlab
device:     cuda:0
```

已验证的真实 VLM smoke 完成 225 个 50 Hz control tick（4.5 秒模拟时间）、3 次 NaVILA decision、118 次 gRPC pose push 和 10 次 RGB capture，最终证据在 `outputs/final_go2_grpc_navila/measurements.json`。画面是程序化诊断图，故该结果明确保持 `scene_fidelity=false`。

该档必须使用 `scene_fidelity=false`。

### 3. 真实 OrcaLab UpdateLocalEnv 与 Go2 ego camera

生产 adapter 将程序化 renderer 替换成 `OrcaLabRenderBridge`：

- `OrcaLabBatchRenderer` 发布 Go2 actor、下载 OrcaLab 编译后的 combined MJCF，并通过 OrcaGym `UpdateLocalEnv` 推送 qpos。
- `/navila_ego` 使用 `prefabs/agentcamera`，配置为 512×512、RGB-only。每次推送状态前按 `T_world_camera = T_world_base × T_base_camera` 更新其世界位姿；OrcaLab 公共 Actor API 不能把独立 actor 直接设为 Go2 asset 内部 `base_link` 的 child，所以当前采用数值等价的刚性跟随。
- 默认通过 edit service `GetCameraDataPNG` 拉取该 ego camera 的新 RGB，CLI 名为 `--camera-transport grpc-png`。
- `--camera-transport websocket` 仍保留给启用了 H.264 packet streaming 的 OrcaStudio build，并继续使用严格递增的 frame fence。

真实 pose/RPC 路径已经验证：Go2 asset 发布成功，local `qpos[1,19]` 映射到 combined `nq=26`，连续 12 次 `UpdateLocalEnv` 无错误，证据在 `outputs/final_orcalab_pose_smoke.json`。该验证不要求 Matterport 资产或相机：

```bash
./scripts/start_orcalab_gui.sh
# GUI 加载完成后，在第二个终端：
./scripts/run_orcalab_pose_smoke.sh
```

完整 Orca 图像路径现在会自动创建/复用并跟随 ego camera：

```bash
navila-orca run \
  --render-backend orcalab \
  --orcagym-address 127.0.0.1:50051 \
  --orcalab-edit-address 127.0.0.1:50151 \
  --camera-transport grpc-png \
  --vlm-backend tcp \
  --output outputs/orcalab_navila
```

本机真实 smoke 已完成：Go2 reset qpos 经 `UpdateLocalEnv` 推入 OrcaLab，`/navila_ego` 随 base 更新后取得前向 512×512 RGB，runner 完成一次 decision 并正常退出。结果见 `outputs/camera_bind_smoke/measurements.json`，首帧在同目录的 `frames/` 下。数值核对中相机位置最大绝对误差为 `1.33e-8 m`，四元数绝对点积为 `0.9999999999999998`。

普通 47D Go2 locomotion policy 与 ego camera 的联合 smoke 也已在同一个 OrcaLab 实例中完成：25 个 control step、16 次 pose push、6 张真实 512×512 RGB、移动 `0.148 m`，pipeline 正常以 `stop` 退出。结果见 `outputs/camera_locomotion_smoke/measurements.json`；该验证不使用 heightmap 或 depth 输入。

同一最小验证已封装为 `./scripts/run_orcalab_camera_smoke.sh`。它只连接已经运行的那一个 OrcaLab，不会启动第二个实例，并且固定使用 `--no-publish`，不会清空当前 layout。

对已经手工摆放物体的当前 layout，使用普通 locomotion 做前进/停止测试：

```bash
./scripts/run_orcalab_scene_locomotion.sh
```

该场景入口把相机放在 Go2 前端头部上方（base frame `0.30 0.00 0.16`），并启用
yaw-only 水平稳定，隔离步态带来的 base roll/pitch。每个动作 chunk 结束后会在终端、
实时窗口和 measurement JSON 的 `motion_chunks` 中记录理想/实测距离、转角、前向进度与
横向漂移，便于区分 VLM 反复修正和 locomotion 跟踪误差。

场景 prompt 按原版 NaVILA 的长任务方式作为一条完整指令发送：依次经过红橙色筒、蓝色
油桶，最后到黄色卡车前停止。每次决策都使用同一条完整指令和从 episode 开始累计的八帧
均匀历史；中间地标不要求输出 `stop`，也不清空历史。只有黄色卡车前的最终 `stop` 才结束
episode。staged waypoint CLI 仍保留用于独立实验，但不再由该场景 launcher 启用。

每次 episode reset 后，Go2 默认先按原版 NaVILA 评测流程执行 100 个零速度 low-level
policy step（50 Hz 下约 2 秒），再从 `step_id=0` 开始导航和里程统计。调试时可通过
`--warmup-steps 0` 禁用，或传入其他非负步数覆盖默认值。

该入口要求当前 combined XML 中恰好有一个完整 Go2，按关节名自动识别 actor，保留其
XY/yaw，并把下载 XML 的错误 scene option 修正到单独副本。具体前提、输出和物理边界见
[当前场景 locomotion 测试](SCENE_LOCOMOTION_TEST.md)。

当前安装的 OrcaStudio 虽会启动 7070 WebSocket 和 NVENC，但其 engine build 没有把编码后的 packet 写入 `StreamingHandler`，所以连接可建立而帧序号保持 0。`grpc-png` 是该 build 的可运行路径；它是文件式拉帧，适合当前 pipeline smoke，不是最终高频生产 transport。修复 engine streaming 后可切换为 `--camera-transport websocket`。

## OrcaLab 启动前提

OrcaLab 是 GUI 程序，必须有可用显示会话和图形 GPU。已验证的本机 workspace 是 `/home/user/Orca/OrcaLab/DefaultProject`，启动命令已封装为 `scripts/start_orcalab_gui.sh`，等价于：

```bash
conda activate orcalab
orcalab /home/user/Orca/OrcaLab/DefaultProject \
  --scene orcalab_day --layout blank --full-screen \
  --sim-config external --verbose
```

同一 workspace 只运行一个 OrcaLab 实例；若 GUI 已在运行，直接复用，不要再次启动。

`--sim-config` 仅在 `--full-screen` 下生效；配置名必须存在。OrcaLab CLI 的 `--port` 是 URL service 端口（默认 50651），不是传给 `OrcaLabRenderBridge` 的 OrcaGym gRPC 地址。后者必须由实际 workspace/runtime 配置确认，OrcaGym 示例通常使用 `localhost:50051`。

启动导航前逐项确认：

- OrcaLab 已完成 scene 编译，OrcaGym gRPC endpoint 可连接。
- Go2 asset 能发布，且 combined MJCF 中存在 free joint 和全部带 actor 前缀的关节。
- 使用默认 `grpc-png` 时，edit service 必须能识别 `AgentCamera` 并返回 color capture；使用 `websocket` 时，还必须确认目标 build 实际发送 H.264 packet，而不只是端口监听成功。
- VLM server 已在目标 host/port 监听，默认是 `localhost:54321`。
- `joint_qpos_addr` 来自同一个 Go2 MJLab 模型，不能手写猜测顺序。

OrcaLab workspace 和 CLI 的官方说明见 [OrcaLab](https://github.com/openverse-orca/OrcaLab)。OrcaGym 的 Gymnasium、分布式 gRPC 和渲染能力见 [OrcaGym](https://github.com/openverse-orca/OrcaGym)。相机 monitor 和数据采集端口模式可参考 [OrcaManipulation](https://github.com/openverse-orca/OrcaManipulation)。

## 训练 smoke

版本检查：

```bash
./scripts/run_training_smoke.sh --check-only
```

真实的一轮通用训练 smoke：

```bash
./scripts/run_training_smoke.sh \
  --python /home/user/anaconda3/envs/orcalab/bin/python \
  --device cuda:0 \
  --num-envs 64 \
  --output /home/user/VLN/NaVILA-Orca/outputs/training_smoke
```

本机验证结果为 64 environments × 24 steps = 1536 samples，一轮 PPO 约 3376 steps/s，并写出 `outputs/training_smoke/model_final.pt`。该命令使用明确 argv 和 `subprocess`，固定 `--iterations 1`、禁用 W&B。它不等于 Go2 训练。若未来需要 native OrcaLab-RSLRL Go2 training task，应先将 Unitree Go2 asset、MDP terms、runner config 和 task registration 正式移植，再更改这一说明。

## 上游参考

- [openverse-orca/OrcaLocomotion](https://github.com/openverse-orca/OrcaLocomotion)
- [openverse-orca/OrcaGym](https://github.com/openverse-orca/OrcaGym)
- [openverse-orca/OrcaLab](https://github.com/openverse-orca/OrcaLab)
- [openverse-orca/OrcaManipulation](https://github.com/openverse-orca/OrcaManipulation)
- [unitreerobotics/unitree_rl_mjlab](https://github.com/unitreerobotics/unitree_rl_mjlab)
- [mujocolab/mjlab](https://github.com/mujocolab/mjlab)
- [unilabsim/UniLab](https://github.com/unilabsim/UniLab)

UniLab 可参考其 backend adapter 与进程间 rollout 设计，但当前官方 main 是 MuJoCoUni/Motrix 路线，不应据此声称本项目使用了 UniLab 的 MJWarp backend。
