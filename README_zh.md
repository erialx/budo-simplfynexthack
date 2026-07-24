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
  在 OrcaLab 中运行视觉语言导航的示例。
  <br />
  <a href="#quickstart">🚀 快速开始</a> ·
  <a href="#competition-baseline">🏁 竞赛基线</a> ·
  <a href="NaVILA-Orca/docs/GETTING_STARTED.md">📚 文档</a>
</p>

<p align="center">
  <img src="NaVILA-Orca/assets/presentation/warehouse-overview.png" alt="OrcaLab 仓库导航场景" width="48%" />
  <img src="NaVILA-Orca/assets/presentation/live-monitor.png" alt="Orca_VLN 实时监视器" width="48%" />
</p>

> **Orca_VLN 提供可运行的 VLN 基线；可在此基础上针对具体任务进行微调。**
> NaVILA 根据语言和第一视角 RGB 生成下一步导航动作；OrcaLab 更新场景并返回下一帧视觉观测。

```text
指令 + 第一视角 RGB  →  NaVILA  →  导航动作  →  OrcaLab  →  下一帧第一视角 RGB
```

本仓库提供示例的 OrcaLab 一侧：持续第一视角观测、场景生命周期、默认仓库回合、可运行的控制基线和可追溯的运行产物。NaVILA 保持在独立环境中，通过 TCP 连接。

## 👁️ 第一视角观测与仿真视图

每一行都将智能体第一视角观测（左）与对应的第三人称仿真视图（右）配对展示。

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

```bash
git clone https://github.com/openverse-orca/Orca_VLN.git
cd Orca_VLN/NaVILA-Orca

# A — OrcaLab
conda activate orcalab
python -m pip install -e '.[orca]'
./scripts/start_orcalab_gui.sh

# B — NaVILA 服务
conda activate navila
export NAVILA_SERVER_SCRIPT=/path/to/NaVILA-Bench/scripts/vlm_server.py
export NAVVLM_MODEL_PATH=/path/to/navvlm-llama3-8b-8f
./scripts/start_navvlm_server.sh

# C — Orca_VLN
conda activate orcalab
./scripts/run_orcalab_scene_locomotion.sh
```

先在 OrcaLab 中打开 `IndustrialWarehouse1_3dgs`，再导入 [`default_set.json`](NaVILA-Orca/default_set.json)。该配置会实例化默认回合所使用的参考物体。

<a id="competition-baseline"></a>

## 🏁 竞赛基线

默认回合要求机器人接近蓝色桶，并在黄色车辆前停止。它让整条闭环可被直接观察：指令、NaVILA 响应、已执行动作、第一视角相机帧和保存的测量结果。

| 评测维度 | 队伍可优化的内容 | 基线状态 |
| --- | --- | --- |
| **高层 VLN** | prompt、任务逻辑、巡检行为、SFT/LoRA | NaVILA 动作闭环已可运行 |
| **低层控制** | 命令跟踪、转向、停止、稳定性、恢复 | 提供的控制模型刻意保持通用，未针对导航调优 |
| **系统证据** | 场景配置、相机采集、动作轨迹、可复现性 | 运行产物自动保存 |

提供的控制模型是保守的平地基线。它没有针对当前仓库、NaVILA 的离散动作片段或特定任务的停车精度进行调优。这一差距是有意保留的：低层执行质量是竞赛指标，而不是被隐藏的实现细节。

## 🧩 扩展基线

- [快速上手](NaVILA-Orca/docs/GETTING_STARTED.md) — 场景配置、进程、相机和首次运行。
- [Hackathon 基线](NaVILA-Orca/docs/HACKATHON_BASELINE.md) — 检查点、赛道、证据和提交范围。
- [高层 VLN](NaVILA-Orca/docs/VLN_FINE_TUNING.md) — 经审核 rollout 的导出及 SFT/LoRA 方向。
- [低层接入](NaVILA-Orca/docs/LOW_LEVEL_LOCOMOTION.md) — 可使用 OrcaLocomotion、IsaacLab 或其他平台训练，再通过稳定 adapter 对齐模型。
- [架构](NaVILA-Orca/docs/ARCHITECTURE.md) — 高层 VLN ↔ 低层运动控制的接口约定。

```bash
# 检查随包提供的控制模型、XML 与版本对齐。
./scripts/check_mjlab_alignment.sh

# 导出基线 rollout，供高层数据审核。
python scripts/export_vln_sft_records.py outputs/warehouse_baseline \
  --output data/vln_review_queue.jsonl
```

## 📦 分发包

`NaVILA-Orca/` 包含运行时、默认全局设置、仓库回合、机器人资源和基线 checkpoint。使用下列命令构建干净的分发包：

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
