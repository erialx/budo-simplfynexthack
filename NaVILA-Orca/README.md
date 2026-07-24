# NavVLM × OrcaLab：面向教学的自主导航实验

本项目是一套可复现的 OrcaLab 课程案例。学生给 Go2 一句自然语言指令，NaVILA 根据持续采集的第一视角 RGB 图像提出下一步动作；本项目把动作变成 Go2 的速度命令，并在 OrcaLab 工业仓库中展示执行过程。

它的边界很明确：

- 本项目负责 **OrcaLab 场景、Go2 locomotion、相机、导航循环、结果记录和教学案例**。
- NaVILA 是独立安装的视觉语言模型服务；本项目通过本地 TCP 协议向它请求动作，**不复制、不打包 NaVILA/LLaVA 源码**。
- 项目不包含也不调用 IsaacLab。

如果你是第一次使用，请从 [学生实验指南](docs/STUDENT_GUIDE.md) 开始；它给出了三终端的完整步骤、每一步应看到的现象及排错方式。

## 你将学到什么

完成默认案例后，学生能够：

1. 区分“高层语言决策”和“低层四足行走策略”的职责。
2. 在 OrcaLab 中加载 3DGS 工业仓库并导入 global setting。
3. 让 `mujococamera1080` 作为持续开启的 Go2 头部 RGB 相机。
4. 观察 NaVILA 的文本动作如何转换成精确的 `vx / vy / wz / duration` 控制。
5. 修改指令、相机外参、目标位置和 Go2 训练轮数，完成自己的实验设计。

## 系统如何协作

```text
自然语言指令 + 最近 8 帧 Go2 RGB
                 │
                 ▼
          NaVILA 服务（独立安装）
                 │  "move forward 25 cm" / "turn left 15 degrees" / "stop"
                 ▼
     NavVLM–OrcaLab 动作解析与导航循环
                 │
                 ▼
       本地 Go2 MJLab locomotion 策略（50 Hz）
                 │
       ┌─────────┴─────────┐
       ▼                   ▼
  MuJoCo/MJWarp       OrcaLab 工业仓库
  计算低层运动        更新 Go2 位姿并采集 RGB
```

NaVILA 不直接控制 12 个关节；它只输出可读的导航动作。Go2 策略负责保持平衡和执行速度。这种分层是本案例最重要的教学点。

## 项目内容

| 路径 | 学生会用到的内容 |
| --- | --- |
| `default_set.json` | OrcaLab global setting：Go2、箱子、蓝桶、黄色车辆 |
| `scenes/default_warehouse/demo_episode.json` | 默认任务的起点、目标、指令和参考路径 |
| `src/navila_orca/` | OrcaLab ↔ Go2 ↔ NaVILA 的适配代码 |
| `src/navila_orca/go2_task/` | Go2 的本地 MJLab 任务、MJCF 和网格 |
| `src/navila_orca/assets/checkpoints/go2_flat.pt` | 本项目的默认平地 Go2 策略检查点 |
| `scripts/` | 启动 GUI、NaVILA 服务、导航、训练和打包的命令 |
| `docs/` | 相机实现说明与学生实验指南 |

工业仓库 3DGS 资产与 NaVILA 模型权重不在仓库中。前者须在 OrcaLab 资产库订阅，后者须按课程提供的 NaVILA 安装说明准备。

## 15 分钟跑通默认案例

### 0. 准备两个环境

- `orcalab` 环境：安装 OrcaLab/OrcaGym `26.6.3`、MJLab 和本项目。
- `navila` 环境：按课程提供的方法安装 NaVILA 和模型权重。

```bash
cd NaVILA-Orca
conda activate orcalab
python -m pip install -e '.[orca]'
```

### 1. 在 OrcaLab 准备场景（终端 A）

```bash
./scripts/start_orcalab_gui.sh
```

在 GUI 中加载 `IndustrialWarehouse1_3dgs`，然后用 global setting 的导入功能选择项目根目录的 `default_set.json`。完成后应看到一台 Go2、蓝色桶和黄色车辆。

### 2. 启动课程提供的 NaVILA 服务（终端 B）

```bash
conda activate navila
export NAVILA_SERVER_SCRIPT=/absolute/path/to/NaVILA-Bench/scripts/vlm_server.py
export NAVVLM_MODEL_PATH=/absolute/path/to/navvlm-llama3-8b-8f
export NAVVLM_PYTHON="$(command -v python)"
./scripts/start_navvlm_server.sh
```

服务默认监听 `127.0.0.1:54321`。若课程使用不同 server 文件，只需把 `NAVILA_SERVER_SCRIPT` 改为实际路径；本项目不假设 NaVILA 在哪个目录。

### 3. 开始导航（终端 C）

```bash
conda activate orcalab
./scripts/run_orcalab_scene_locomotion.sh
```

导航过程写到 `outputs/scene_locomotion_smoke/`。先看终端中的动作文本，再打开保存的 RGB 帧，核对“图像 → 动作 → 位姿”是否一致。

## 三类常用实验

| 实验 | 改什么 | 观察什么 |
| --- | --- | --- |
| 语言实验 | `--instruction 'Move to the blue barrel and stop.'` | NaVILA 的动作序列是否随目标改变 |
| 视觉实验 | `--camera-mount-position X Y Z` | 头部相机视角改变后，VLM 的转向是否变化 |
| 控制实验 | `--warmup-steps` 或 `--checkpoint` | 起步稳定性、速度跟踪与轨迹平滑度 |

详尽步骤、任务清单和错误定位见 [学生实验指南](docs/STUDENT_GUIDE.md)。

## 训练与打包

训练本项目的 Go2 平地策略：

```bash
./scripts/train_go2.sh --agent.max-iterations 15001
```

生成无缓存、无输出文件的学生压缩包：

```bash
./scripts/build_student_kit.sh
```

工程结构参考 [OrcaLocomotion `orca_warp`](https://github.com/openverse-orca/OrcaLocomotion/tree/orca_warp)。
