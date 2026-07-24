<p align="right"><sub><a href="README.md">English</a> · <strong>中文</strong></sub></p>

# Orca_VLN 开发包

此目录是可分发的 OrcaLab 运行时。GitHub 项目主页位于上一级：[Orca_VLN](../README_zh.md)。

首次运行请阅读[快速上手指南](docs/GETTING_STARTED.md)。以下三条命令覆盖正常开发流程：

```bash
./scripts/start_orcalab_gui.sh
./scripts/start_navvlm_server.sh
./scripts/run_orcalab_scene_locomotion.sh
```

运行导航前，请在已打开的工业仓库 3DGS 场景中导入 `default_set.json`。NaVILA 本身仍是课程单独提供的服务；通过 `NAVILA_SERVER_SCRIPT` 配置其服务文件，并通过 `NAVVLM_MODEL_PATH` 配置模型目录。

```bash
./scripts/build_kit.sh
```

该开发包包含 OrcaLab adapter、本地 Go2 task/MJCF/mesh 资源、默认 Go2 运动 checkpoint、全局设置和一个可复现的仓库回合。它不包含 NaVILA/LLaVA 源码、模型权重、3DGS 资源或 IsaacLab。
