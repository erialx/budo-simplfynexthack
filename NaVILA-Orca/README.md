# NavVLM–OrcaLab 学生复现实验包

这是一个**独立的 OrcaLab 自动导航案例**：NavVLM 根据 Go2 头部 RGB 相机图像和自然语言指令输出动作，MJLab Go2 策略负责执行动作，OrcaLab 负责工业仓库场景、机器人和相机渲染。

该目录不依赖同级的 NaVILA-Bench、NaVILA、unitree_rl_mjlab、orca_rl 或 OrcaLab-RSLRL 检出目录；运行所需的项目源码、Go2 任务、Go2 策略检查点、默认 global setting 和演示场景都在本目录中。**不包含、不调用 IsaacLab。**

完整的从零复现流程见 [docs/STUDENT_GUIDE.md](docs/STUDENT_GUIDE.md)。

## 包含内容

| 位置 | 用途 |
| --- | --- |
| `default_set.json` | 默认 OrcaLab global setting：Go2、箱子、蓝色桶和黄色车辆 |
| `scenes/default_warehouse/demo_episode.json` | 工业仓库导航示例的起点、目标与指令 |
| `src/navila_orca/` | 导航循环、OrcaLab 渲染桥、`mujococamera1080` 常驻 RGB 相机、场景 profile 注入 |
| `src/navila_orca/go2_task/` | 本地 Go2 MJLab 任务定义、MJCF 和网格 |
| `src/navila_orca/assets/checkpoints/go2_flat.pt` | 默认 Go2 平地行走策略检查点 |
| `src/llava/`、`src/navila_orca/navvlm_server.py` | NavVLM 推理服务源码和 TCP 服务入口 |
| `scripts/` | 启动 OrcaLab、NavVLM、导航以及 Go2 训练的显式脚本 |

NavVLM 8B 权重没有随包附带；请按其原始发布渠道获得模型，并设置 `NAVVLM_MODEL_PATH`。工业仓库 3DGS 资产也需由用户在 OrcaLab 资产库订阅/下载；`default_set.json` 是该场景加载后要导入的 global setting，不会代替 3DGS 场景资产。

## 最短运行路径

```bash
cd NaVILA-Orca
conda activate orcalab
python -m pip install -e '.[orca]'

# 终端 1：打开 OrcaLab；导入 default_set.json 的操作见学生指南
./scripts/start_orcalab_gui.sh

# 终端 2：在单独的 navvlm 环境中启动服务（填写你实际下载的模型目录）
conda activate navvlm
export NAVVLM_MODEL_PATH=/absolute/path/to/navvlm-llama3-8b-8f
export NAVVLM_PYTHON="$(command -v python)"
./scripts/start_navvlm_server.sh

# 终端 3：运行默认工业仓库导航案例
./scripts/run_orcalab_scene_locomotion.sh
```

默认脚本使用 OrcaLab/OrcaGym `26.6.3`、Go2 actor 自动发现、`prefabs/mujococamera1080` 和 `orca-train` MuJoCo profile。场景切换后，`start_orcalab_gui.sh` 启动的 watcher 会再次注入 profile；不会改写 OrcaLab 的安装目录。

## 自己训练

Go2 locomotion 训练入口完全使用本项目内的任务定义：

```bash
./scripts/train_go2.sh --agent.max-iterations 15001
```

训练环境要求见学生指南。NavVLM 的微调需要另行准备图文/轨迹数据和基础模型；本仓库提供本地 `llava` 训练源码，但不会声称附带一个可直接重训的 NavVLM 数据集或权重。

## 打包给学生

```bash
./scripts/build_student_kit.sh
```

生成 `dist/navvlm-orcalab-student-kit.tar.gz`。归档含 Go2 checkpoint、源码、默认 setting 和文档；不含运行输出、缓存、模型权重及任何 IsaacLab 目录。

## 参考与许可

工程结构和 OrcaLab/MJLab 使用方式参考 [OrcaLocomotion 的 `orca_warp` 分支](https://github.com/openverse-orca/OrcaLocomotion/tree/orca_warp)。上游 NavVLM/NaVILA 许可文本保存在 `third_party/licenses/`；使用或再分发前请遵守各上游项目、OrcaLab 资产和模型权重的许可条款。
