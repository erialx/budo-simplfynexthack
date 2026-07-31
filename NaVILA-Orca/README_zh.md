<p align="right"><sub><a href="README.md">English</a> · <strong>中文</strong></sub></p>

# Orca_VLN 开发包

此目录是可分发的 OrcaLab 运行时。GitHub 项目主页位于上一级：[Orca_VLN](../README_zh.md)。

首次运行请阅读[快速上手指南](docs/GETTING_STARTED_zh.md)。全新 checkout
先执行一次 `./scripts/setup_all.sh`，并确认 `./scripts/doctor.sh` 全部通过。
此后无需激活 Conda 环境或设置仓库根目录变量。以下三条命令覆盖正常开发流程：

```bash
./scripts/start_orcalab_gui.sh
./scripts/start_navvlm_server.sh
./scripts/run_orcalab_scene_locomotion.sh
```

运行导航前，请先在 OrcaLab 中订阅 `VLN_Presentation`
（`333f1b37-518d-44ed-ba1c-89b80071074f.pak`）和 `unitree_robots`，然后选择
`VLN_Presentation` 场景，并执行 **文件 → 打开布局 → `factory.json`**。
启动器默认 prompt 会依次经过红色垃圾桶、右转至蓝色油桶，并在白色机械臂前停止。
NaVILA 使用独立运行环境；TCP 服务适配器由本项目提供，模型位于项目默认模型目录。

```bash
./scripts/build_kit.sh
```

该开发包包含 OrcaLab adapter、本地 Go2 task/MJCF/mesh 资源、默认 Go2 运动 checkpoint、`factory.json` 布局和一个可复现的 `VLN_Presentation` 回合。它不包含 NaVILA/LLaVA 源码、模型权重、已订阅的 OrcaLab 资源或 IsaacLab。
