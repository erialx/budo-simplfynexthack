# 学生复现指南：NavVLM 在 OrcaLab 中控制 Go2

本指南只使用 `NaVILA-Orca` 目录中的源码和资产，不需要安装 IsaacLab，也不需要把其它源码仓库软链接到本项目。

## 1. 准备环境

需要 Linux、NVIDIA GPU、CUDA 对应的 PyTorch，以及 OrcaLab/OrcaGym `26.6.3`。推荐在 OrcaLab 自己的 conda 环境中安装：

```bash
cd /path/to/NaVILA-Orca
conda activate orcalab
python -m pip install -e '.[orca]'
```

此外安装与本地 Go2 训练栈匹配的 `mjlab==1.2.0`、`mujoco-warp==3.5.0` 和 `rsl-rl-lib` 5.x。若你的 OrcaLab Python 不在默认位置，所有脚本均可通过下面变量指定：

```bash
export NAVILA_ORCA_PYTHON=/absolute/path/to/orcalab/bin/python
export NAVILA_ORCA_ORCALAB_BIN=/absolute/path/to/orcalab/bin/orcalab
```

NavVLM 服务建议使用**独立环境**，避免模型所需的 PyTorch/CUDA 版本改写 OrcaLab 环境。先按你的 CUDA 版本安装 PyTorch，再安装本项目的显式 NavVLM 依赖：

```bash
conda create -n navvlm python=3.10 -y
conda activate navvlm
# 先根据本机 CUDA 安装匹配的 torch/torchvision
python -m pip install -e '/path/to/NaVILA-Orca[navvlm]'
```

`navvlm` 依赖组明确列出了 `transformers`、`accelerate`、`s2wrapper` 等模型运行依赖；没有从其它本地项目导入 Python 包。

检查本地文件和安装状态：

```bash
${NAVILA_ORCA_PYTHON:-python} -m navila_orca.cli doctor
${NAVILA_ORCA_PYTHON:-python} -m navila_orca.training --check-only
```

## 2. 获取场景和模型

1. 在 OrcaLab 资产库订阅并下载 `IndustrialWarehouse1_3dgs`（或使用功能相同的工业仓库 3DGS 场景）。该资产不在本压缩包中。
2. 在 OrcaLab 打开该 3DGS 场景后，通过 global setting 的导入功能选择本项目根目录的 [`default_set.json`](../default_set.json)。这会创建案例所需的 Go2、箱子、蓝色桶和黄色车辆。
3. 从 NavVLM 的原始发布渠道取得兼容模型，设置绝对路径：

   ```bash
   export NAVVLM_MODEL_PATH=/absolute/path/to/navvlm-llama3-8b-8f
   ```

`default_set.json` 不是 3DGS 场景本身；它必须在工业仓库场景已经打开后导入。默认案例定义在 [`scenes/default_warehouse/demo_episode.json`](../scenes/default_warehouse/demo_episode.json)，可复制后修改指令、起点和目标。

## 3. 启动 OrcaLab 和常驻相机

在第一个终端启动：

```bash
./scripts/start_orcalab_gui.sh
```

脚本会为 GUI 生命周期启动本项目内的 scene-profile watcher。每次打开新场景时它都会给运行时 MuJoCo XML 注入 `orca-train` 选项（`timestep=0.005`、关闭空气阻力）；不会改 OrcaLab 安装目录。

导航脚本只创建一次 `prefabs/mujococamera1080`，随后反复更新其 transform 并调用 `GetCameraPNG`。它是持续开启的 MuJoCo RGB 相机，安装有 OrcaLab `26.6.3` 即可使用，无需旧版 `agentcamera` MCP 接口。相机默认绑定在 Go2 基座前上方（`0.35, 0.0, 0.48`），并保持地平线稳定。

## 4. 启动 NavVLM 服务

第二个终端（`navvlm` 环境）：

```bash
conda activate navvlm
export NAVVLM_MODEL_PATH=/absolute/path/to/navvlm-llama3-8b-8f
export NAVVLM_PYTHON="$(command -v python)"
./scripts/start_navvlm_server.sh
```

服务监听 `127.0.0.1:54321`。如需其它端口，设置 `NAVVLM_PORT`，并在导航命令中传相同的 `--vlm-port`。

## 5. 运行默认自动导航案例

确认当前 OrcaLab 场景只含一个完整 Go2 actor，且 OrcaGym gRPC 在 `127.0.0.1:50051`。第三个终端运行：

```bash
./scripts/run_orcalab_scene_locomotion.sh
```

该命令读取默认场景说明 `demo_episode.json`，从 NavVLM 接收“前进/转向/停止”文本动作并交给本地 Go2 策略执行。结果（轨迹、相机帧与 JSON）写入 `outputs/scene_locomotion_smoke/`。

常用自定义方式：

```bash
./scripts/run_orcalab_scene_locomotion.sh \
  --scenario scenes/default_warehouse/demo_episode.json \
  --instruction 'Move to the blue barrel and stop.'
```

若场景中有多个 Go2，显式传入 actor 名称：

```bash
./scripts/run_orcalab_scene_locomotion.sh --robot-actor-name go2_000
```

## 6. 训练 Go2 locomotion 策略

Go2 任务、MJCF、网格和默认检查点都已放在 `src/navila_orca/` 内。先做环境检查：

```bash
./scripts/train_go2.sh --help
```

开始训练：

```bash
./scripts/train_go2.sh --agent.max-iterations 15001
```

训练日志由 MJLab 写入当前工作目录下的 `logs/`；它们不会进入发行压缩包。训练完成后，把输出 checkpoint 路径传给导航命令的 `--checkpoint`。

## 7. 微调 NavVLM（可选）

`src/llava/` 是项目内的视觉语言训练源码。微调之前，需要你自行准备合法可用的图像/视频、语言指令、轨迹/动作监督数据，以及基础模型权重。建议先以默认案例收集 OrcaLab RGB 帧和动作序列，再按所用 NavVLM 发布版本的训练配置进行 SFT。由于数据集、模型授权和 GPU 规模会因课程而变，本包不捏造一条“无需数据即可训练”的命令。

## 常见问题

- `Actor does not exist`：先在 OrcaLab 导入 `default_set.json`，再运行导航；检查只有一个完整 Go2。
- 相机属性缺失：确认是 `prefabs/mujococamera1080`，且 `orca-lab`、`orca-gym` 均为 `26.6.3`。
- `Go2 checkpoint does not exist`：确认 `src/navila_orca/assets/checkpoints/go2_flat.pt` 仍在发行包中，或传 `--checkpoint /path/to/file.pt`。
- VLM 无法连接：先启动 `start_navvlm_server.sh`，并核对 `NAVVLM_PORT` 与导航参数一致。
