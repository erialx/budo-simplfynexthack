<p align="right"><sub><a href="README.md">English</a> · <strong>中文</strong></sub></p>

<p align="center">
  <img src="NaVILA-Orca/assets/brand/orca-vln-navigation-logo.png" alt="Orca_VLN 四足机器人与导航轨迹" width="150" align="middle" />
  &nbsp;&nbsp;
  <img src="NaVILA-Orca/assets/brand/orca-platform-logo-blue.png" alt="松应科技 ORCA Lab" width="125" align="middle" />
</p>

<h1 align="center">
  <img src="NaVILA-Orca/assets/brand/orca-vln-wordmark.svg" alt="ORCA VLN" width="340" />
</h1>

<p align="center">
  一个运行于 OrcaLab 的视觉语言导航示例。
  <br />
  <a href="#quickstart">🚀 快速开始</a> ·
  <a href="#competition-baseline">🏁 竞赛基线</a> ·
  <a href="NaVILA-Orca/docs/GETTING_STARTED_zh.md">📚 文档</a>
</p>

<p align="center">
  <img src="NaVILA-Orca/assets/presentation/factory-overview.png" alt="OrcaLab 工厂导航场景" width="72%" /><br />
  <sub><strong>工厂导航场景</strong></sub>
</p>

<p align="center">
  <img src="NaVILA-Orca/assets/presentation/factory-live-monitor.png" alt="Orca_VLN 工厂场景实时监视器" width="57.8%" /><br />
  <sub><strong>实时导航监视器</strong></sub>
</p>

> **Orca_VLN 是一套可直接运行的 VLN 基线，可在此基础上针对具体任务继续微调。**
> NaVILA 读取自然语言指令和一段第一视角 RGB 观测，给出下一步导航动作；OrcaLab 执行该动作并返回新的视觉观测。

```text
指令 + 第一视角 RGB  →  NaVILA  →  导航动作  →  OrcaLab  →  下一帧第一视角 RGB
```

本仓库包含示例在 OrcaLab 中运行所需的部分：持续输出第一视角观测、管理场景生命周期、提供预置仓库任务与可运行的控制基线，并保存可追溯的运行记录。NaVILA 保持在独立环境中，通过 TCP 连接。

## 👁️ 第一视角与仿真视图

每一行展示同一任务的两种视角：左侧为智能体第一视角，右侧为对应的第三人称仿真画面。

<p align="center">
  <img src="NaVILA-Orca/assets/presentation/kitchen-overview.png" alt="厨房场景第一视角" width="48%" />
  <img src="NaVILA-Orca/assets/presentation/kitchen-robot-view.png" alt="厨房场景中的机器人" width="48%" />
</p>

<p align="center">
  <img src="NaVILA-Orca/assets/presentation/warehouse-corridor.png" alt="仓库走廊第一视角" width="48%" />
  <img src="NaVILA-Orca/assets/presentation/warehouse-robot-view.png" alt="仓库场景中的机器人" width="48%" />
</p>

<p align="center">
  <img src="NaVILA-Orca/assets/presentation/storage-aisle.png" alt="货架区域第一视角" width="48%" />
  <img src="NaVILA-Orca/assets/presentation/storage-robot-view.png" alt="货架区域中的机器人" width="48%" />
</p>

<a id="quickstart"></a>

## 🚀 快速开始

**开始前：** 使用 Ubuntu 22.04/24.04、Git，以及至少 RTX 4090 级别、能通过
`nvidia-smi` 检查的 NVIDIA GPU 与驱动。

### 一次性安装

如果 `conda --version` 无法运行，直接安装一套干净的 Miniconda：

```bash
curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh \
  -o /tmp/miniconda.sh
bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda init bash
conda --version
```

克隆项目：

```bash
git clone https://github.com/openverse-orca/Orca_VLN.git
cd Orca_VLN
```

创建两套锁定环境，并下载经过验证的 NaVILA 模型：

```bash
./NaVILA-Orca/scripts/setup_all.sh
```

全新 Ubuntu 首次安装时，脚本可能请求一次 `sudo`，用于安装 OrcaLab GUI
所需的 Qt/XCB 系统库。
脚本也会在首次打开 GUI 前准备好 OrcaLab 官方原生 viewport 和场景 pak，
避免 OrcaLab 在首启过程中临时安装组件并要求重启。

单独检查安装结果。最后一行必须是
`Orca_VLN installation is ready.`：

```bash
./NaVILA-Orca/scripts/doctor.sh
```

两套环境都位于当前 checkout 的 `.conda/envs/`：OrcaLab 使用 Python
3.12，NaVILA 使用 Python 3.10。启动器根据自己的文件位置定位环境，
因此不需要设置 `ORCA_VLN_ROOT`，也不用手动执行 `conda activate` 或
`deactivate`。

### 按顺序在三个终端运行

#### A — 打开 OrcaLab 并组成预设场景

```bash
./NaVILA-Orca/scripts/start_orcalab_gui.sh
```

此时不要运行导航。在 OrcaLab 中：

1. 在 OrcaLab 资产浏览器中订阅 `VLN_Presentation`
   （`333f1b37-518d-44ed-ba1c-89b80071074f.pak`）和 `unitree_robots`，等待两个
   订阅均显示为最新。
2. 在场景选择器中选择 `VLN_Presentation`。
3. 选择 **文件 → 打开布局**，选中
   [`NaVILA-Orca/factory.json`](NaVILA-Orca/factory.json)。
4. 等待 Go2、红色高圆柱垃圾桶、蓝色油桶和白色机械臂都出现在工厂场景中。

> **资产订阅——默认案例的最低要求。** `factory.json` 引用了
> `vln_presentation` 资产族和 Go2 prefab。`VLN_Presentation` 提供工厂、垃圾桶、
> 油桶、工作台、隔断和纸箱，`unitree_robots` 提供 Go2。两个订阅完成前不要加载
> 布局。

`VLN_Presentation` 提供场景本体，`factory.json` 在其上加入已编排的布局。完成后
保持终端 A 和 OrcaLab 运行。

**已经在使用 OrcaLab？** 可以跳过终端 A，直接使用自己已打开的兼容
OrcaLab GUI（本基线验证版本为 OrcaLab 26.6.3）。只需在该 GUI 中选择
`VLN_Presentation`，并载入同一个 `factory.json` 布局文件。

#### B — 启动 NaVILA 服务

```bash
./NaVILA-Orca/scripts/start_navvlm_server.sh
```

等待终端 B 显示正在监听 `127.0.0.1:54321`。

#### C — 启动闭环导航

只有 OrcaLab 中已显示完整预设场景、B 中服务已开始监听后，才能运行 C。
先在 OrcaLab GUI 中依次选择：**运行 → 开始模拟 → 无仿真程序 → 启动**，
等待外部仿真开始运行。终端 C 只连接这个已启动的会话，不会自行打开
OrcaLab 或启动仿真：

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh
```

该命令会读取
[`NaVILA-Orca/prompts/orcalab_scene_locomotion.txt`](NaVILA-Orca/prompts/orcalab_scene_locomotion.txt)
中的默认 prompt。下面的显式写法与默认值等价，适合核对当前指令：

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh \
  --instruction "Walk toward the tall red cylindrical waste bin and pass close by it without stopping. As soon as you have passed the red bin, turn right and keep turning until the large blue metal oil barrel is visible in front of you. Walk toward the blue barrel and pass close by it without stopping. Only after you have reached the blue barrel, continue toward the white robotic arm at the far end. Approach the front of the white robot arm and stop only when you are close to its front. Follow this exact order: red bin, right turn, blue barrel, white arm."
```

<a id="competition-baseline"></a>

## 🏁 竞赛基线

默认任务要求机器人经过红色垃圾桶、右转、经过蓝色油桶，最后在白色机械臂前停止。这条完整闭环的每一步都可直接观察：指令、NaVILA 响应、实际执行的动作、第一视角相机帧和保存的测量结果。

| 评测维度 | 可优化方向 | 基线状态 |
| --- | --- | --- |
| **高层 VLN** | Prompt、任务逻辑、巡检行为、SFT/LoRA | NaVILA 动作闭环可直接运行 |
| **低层控制** | 命令跟踪、转向、停止、稳定性、恢复 | 提供的控制模型刻意保持通用，未针对导航调优 |
| **系统与证据** | 场景配置、相机采集、动作轨迹、可复现性 | 运行记录自动保存 |

提供的控制模型是保守的平地基线，并未针对当前仓库、NaVILA 的离散动作片段或特定任务的停车精度进行调优。这一差距是有意保留的：低层执行质量本身就是竞赛指标，而不是需要被隐藏的实现细节。

## 🧩 进阶方向

- [快速上手](NaVILA-Orca/docs/GETTING_STARTED_zh.md) — 场景配置、进程、相机与首次运行。
- [竞赛基线](NaVILA-Orca/docs/HACKATHON_BASELINE_zh.md) — 检查点、赛道、证据与提交范围。
- [高层 VLN](NaVILA-Orca/docs/VLN_FINE_TUNING_zh.md) — 已审核数据要求，以及 SFT/LoRA 的实践方向。
- [低层接入](NaVILA-Orca/docs/LOW_LEVEL_LOCOMOTION_zh.md) — 可在 OrcaLocomotion、IsaacLab 或其他平台训练，再通过稳定适配器对齐模型。
- [架构](NaVILA-Orca/docs/ARCHITECTURE_zh.md) — 高层 VLN ↔ 低层运动控制的接口约定。

## 📦 打包发布

`NaVILA-Orca/` 包含运行时、`factory.json` 布局、`VLN_Presentation` 任务、机器人资源和基线 checkpoint。使用以下命令构建干净的分发包：

```bash
./scripts/build_kit.sh
```

## 🙌 致谢

Orca_VLN 使用 [NaVILA](https://github.com/AnjieCheng/NaVILA) 作为高层视觉语言导航模型。若在研究中使用 NaVILA，请引用：

```bibtex
@inproceedings{cheng2025navila,
  title     = {Navila: Legged robot vision-language-action model for navigation},
  author    = {Cheng, An-Chieh and Ji, Yandong and Yang, Zhaojing and Gongye, Zaitian and Zou, Xueyan and Kautz, Jan and B{\i}y{\i}k, Erdem and Yin, Hongxu and Liu, Sifei and Wang, Xiaolong},
  booktitle = {RSS},
  year      = {2025}
}
```

## 📄 许可证

本项目采用 [MIT License](LICENSE) 发布。
