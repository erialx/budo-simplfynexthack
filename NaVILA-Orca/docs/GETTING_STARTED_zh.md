<p align="right"><sub><a href="GETTING_STARTED.md">English</a> · <strong>中文</strong></sub></p>

# 快速上手：让 Go2 在 OrcaLab 中听懂导航指令

本实验不是“把模型跑起来”就结束。你要观察一条完整的机器人决策链：**看见什么、语言模型说了什么、四足机器人怎样执行、场景中发生了什么**。

建议把 OrcaLab、NaVILA server 和导航进程分到三个终端，便于定位每一层的问题。

## 一、实验目标与成功标准

默认任务是：`Move forward toward the blue barrel, then stop before the yellow vehicle.`

成功不只等于终端没有报错。完成一次有效实验时，应同时满足：

- OrcaLab 中已有工业仓库、一个完整 Go2、蓝色桶和黄色车辆。
- `mujococamera1080` 的图像会随着 Go2 移动而改变。
- NaVILA server 收到 8 帧图像和任务文本，并返回一条可解析动作。
- Go2 动作平稳，结束后 `outputs/scene_locomotion_smoke/` 内有结果 JSON 与 RGB 帧。

## 二、先理解四个角色

| 角色 | 输入 | 输出 | 不负责什么 |
| --- | --- | --- | --- |
| NaVILA | 8 帧 RGB + 自然语言 | 文本动作 | 关节控制、碰撞求解 |
| 导航循环 | 文本动作 + 当前状态 | 速度命令和持续时间 | 生成视觉语言答案 |
| Go2 locomotion | 速度命令 | 12 关节动作 | 理解“蓝桶”或“左转”语义 |
| OrcaLab | Go2 位姿 | 场景 RGB 与可视化 | 训练或求解低层步态 |

例如，NaVILA 说 `turn left 15 degrees` 后，导航循环把它解析为固定角速度和 0.5 秒持续时间；Go2 策略在 50 Hz 下连续执行，OrcaLab 相机再采集新画面。这就是高层 VLM 与低层控制的分工。

## 三、准备工作

### 1. OrcaLab 环境

需要 Linux、NVIDIA GPU、OrcaLab/OrcaGym `26.6.3`、MJLab `1.2.0`、`mujoco-warp 3.5.0` 和 `rsl-rl-lib 5.x`。

```bash
cd /path/to/NaVILA-Orca
conda activate orcalab
python -m pip install -e '.[orca]'
python -m navila_orca.cli doctor
python -m navila_orca.training
```

`doctor` 中以下四个路径必须为 `exists: true`：默认任务、`default_set.json`、`go2_flat.pt`、Go2 XML。版本不一致时先不要继续做场景实验。

所有启动脚本都会使用执行 `conda activate orcalab` 的终端中的 `CONDA_PREFIX/bin/python`；不会硬编码 Anaconda 或 Miniconda 路径。GUI 可执行文件也从同一个环境解析。只有明确不激活 Conda 时，才需要显式设置以下两个路径：

```bash
export NAVILA_ORCA_PYTHON=/absolute/path/to/orcalab/bin/python
export NAVILA_ORCA_ORCALAB_BIN=/absolute/path/to/orcalab/bin/orcalab
```

### 2. 在兼容的 NaVILA 环境中运行服务

NaVILA 及其模型是本项目的显式外部前提。请保留其专用的 Python 3.10 / PyTorch 2.3 环境；当前 OrcaLab 使用 Python 3.12 / PyTorch 2.12，在其中安装 NaVILA 会替换不兼容的核心包。Orca_VLN 自带轻量 TCP server adapter，不需要 NaVILA-Bench。

不要直接使用 NaVILA 的 `environment_setup.sh`：它创建的是按名称的环境、在所需 PyTorch 之前安装 FlashAttention，并包含 TCP 服务不需要的训练/评测配置。请在工作区根目录按以下方式创建经过验证的推理环境：

本案例需要服务脚本接受这些参数：

```text
--host 127.0.0.1  --port 54321  --model_path /path/to/model
```

```bash
cd /path/to/Orca_VLN
./NaVILA-Orca/scripts/setup_navila_env.sh
./NaVILA-Orca/scripts/setup_navila_env.sh --verify
./NaVILA-Orca/scripts/download_navila_model.sh
```

安装脚本会创建 `/path/to/Orca_VLN/.conda/envs/navila`，检出经过验证的 NaVILA 版本，从 CUDA 12.1 wheel 源安装 PyTorch `2.3.0` / torchvision `0.18.0`，再安装匹配的官方 FlashAttention 2.5.8 wheel 与 NaVILA 的 Transformers 补丁。它不会修改 `orcalab` 环境。只有在确实需要不同目录或已经审核过的不同源码版本时，才在运行前设置 `NAVILA_ENV_PREFIX`、`NAVILA_SOURCE` 或 `NAVILA_REVISION`。

## 四、第一次运行：按顺序做

### 步骤 A：打开默认场景

终端 A：

```bash
./scripts/start_orcalab_gui.sh
```

GUI 中执行：

1. 订阅/下载并打开 `IndustrialWarehouse1_3dgs`。
2. 使用 global setting 的导入功能选择 `NaVILA-Orca/default_set.json`。
3. 在场景树中确认只有一个完整 Go2 actor。
4. 目视确认蓝桶和黄色车辆在前方可见区域。

`default_set.json` 只保存 actor 布局；它不是 3DGS 仓库本体。没有先加载工业仓库，导入 setting 不会产生可用于导航的视觉场景。

启动脚本会附带一个 scene-profile watcher。每次新场景生成 MuJoCo XML 时，watcher 都注入 `orca-train` profile（`timestep=0.005`、关闭空气阻力），不会修改 OrcaLab 安装目录。

### 步骤 B：启动 NaVILA

终端 B：

```bash
conda activate /path/to/Orca_VLN/.conda/envs/navila
./scripts/start_navvlm_server.sh
```

当日志出现服务正在 `127.0.0.1:54321` 监听时，保持此终端运行。若命令报“server file does not exist”，检查 `NAVILA_SERVER_SCRIPT`；若模型加载失败，检查 `NAVVLM_MODEL_PATH` 是否是模型根目录而不是单个权重文件。

### 步骤 C：运行导航

终端 C：

```bash
conda activate orcalab
./scripts/run_orcalab_scene_locomotion.sh
```

脚本的关键默认项：

| 参数 | 默认行为 | 教学含义 |
| --- | --- | --- |
| `--robot-actor-name auto` | 要求场景中恰有一台完整 Go2 | 避免控制到错误 actor |
| `--camera-asset-path prefabs/mujococamera1080` | 创建一次、持续采集 PNG | 看见的是机器人视角，不是 viewport |
| `--camera-mount-position 0.35 0 0.48` | 相机位于基座前上方 | 接近头部视角，降低身体遮挡 |
| `--warmup-steps 100` | 起步前零速度执行 100 个策略步 | 让策略状态稳定后再接收 VLM 命令 |
| `--scene-profile orca-train` | 200 Hz 物理、50 Hz 控制 | 动作距离可以按 tick 精确复现 |

## 五、读懂输出

结果目录为 `outputs/scene_locomotion_smoke/`。每次实验至少保存：

- RGB 帧：检查视角、图像是否随机器人移动而变化。
- 运行 JSON：记录输入 instruction、解析后的动作、时间和轨迹。
- scene alignment 文件：出现坐标或 actor 问题时用于核对 OrcaLab combined XML。

建议每组建立一张实验表：指令、首次模型动作、最终位置、是否接近蓝桶、是否出现误转向、截图文件名。不要只记录“成功/失败”。

## 六、三项递进任务

### 任务 1：复现实验

保持所有默认参数不变，跑两次默认案例。比较两次的动作序列与最终轨迹，讨论模型推理是否完全确定，以及仿真初始化是否可重复。

### 任务 2：语言消融

```bash
./scripts/run_orcalab_scene_locomotion.sh \
  --instruction 'Move to the blue barrel and stop.'
```

再尝试“先向左转，再靠近蓝桶”。记录不同表达是否导致不同动作。注意：这不是测语言模型的常识题，而是观察语言、图像和几何关系是否共同影响决策。

### 任务 3：相机消融

将相机略微提高：

```bash
./scripts/run_orcalab_scene_locomotion.sh \
  --camera-mount-position 0.35 0 0.58
```

比较两组 RGB 帧和 NaVILA 动作。相机位置改变的不是物理控制器，而是 VLM 的观察；因此若结果变化，应该从视觉信息变化解释。

## 七、接入自定义 Go2 policy（进阶）

默认 checkpoint 已足够复现 VLN baseline。若要使用自行训练的 low-level policy，可以选择 [OrcaLocomotion](https://github.com/openverse-orca/OrcaLocomotion)、IsaacLab 或其他训练平台；训练平台不属于本项目的限制范围。

MJLab 在 Orca_VLN 中只负责运行当前 baseline 和输出对齐报告。自定义模型的接入重点是：Go2 关节顺序/符号、根部位姿、动作顺序、控制频率，以及 `vx / vy / wz / duration` 的速度命令接口。详细的直接加载与 adapter 路径见[低层运动控制](LOW_LEVEL_LOCOMOTION_zh.md)。高层 NaVILA 的 SFT/LoRA 路径见[VLN 微调](VLN_FINE_TUNING_zh.md)。

## 八、常见错误：先判断哪一层出了问题

| 现象 | 优先检查 | 常见原因 |
| --- | --- | --- |
| `Actor does not exist` | OrcaLab 场景树 | 未导入 setting、Go2 被删除或 actor 名不匹配 |
| 找到 0/多个 Go2 | 当前 scene | 没有完整 Go2 或重复导入了 setting |
| 相机属性缺失 | `orca-lab` 与 `orca-gym` 版本 | 未使用 26.6.3 或错误使用旧 `agentcamera` |
| VLM 无法连接 | 终端 B、端口 54321 | NaVILA server 未启动、端口不一致 |
| 模型加载失败 | `NAVVLM_MODEL_PATH` | 指向了错误目录或 NaVILA 环境不完整 |
| Go2 抖动/跌倒 | checkpoint、warmup、场景初始位置 | checkpoint 不匹配、起点穿模、尚未稳定 |

排错顺序永远是：场景/actor → 相机 → NaVILA server → 动作文本 → Go2 策略。这样不会把一个连接错误误判为“模型不会导航”。
