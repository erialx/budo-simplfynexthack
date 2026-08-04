<p align="right"><sub><a href="README.md">English</a> · <strong>中文</strong></sub></p>

# Orca_VLN 开发包

此目录是可分发的 OrcaLab 运行时。GitHub 项目主页位于上一级：[Orca_VLN](../README_zh.md)。

部署方式二选一：

| 部署方式 | 结构 | 操作指南 |
| --- | --- | --- |
| **方案 A（默认）— 单机部署** | OrcaLab、NaVILA 与导航进程位于同一台机器 | [快速上手](docs/GETTING_STARTED_zh.md#option-a-single-host) |
| **方案 B — 远程推理** | OrcaLab 与导航进程位于客户端，NaVILA 位于独立 GPU 服务器 | [远程推理部署](docs/REMOTE_INFERENCE_zh.md) |

## 方案 A（默认）— 单机开发流程

全新 checkout 先执行一次 `./scripts/setup_all.sh`，并确认
`./scripts/doctor.sh` 全部通过。此后无需激活 Conda 环境或设置仓库根目录
变量。

以下三条命令覆盖正常的单机开发流程：

```bash
./scripts/start_orcalab_gui.sh
./scripts/start_navvlm_server.sh
./scripts/run_orcalab_scene_locomotion.sh
```

完整的首次运行流程见快速上手指南中的
[方案 A](docs/GETTING_STARTED_zh.md#option-a-single-host)。

## 方案 B — 远程推理

方案 B 将 OrcaLab GUI 和导航进程留在客户端，只在独立 GPU 服务器上运行
NaVILA 服务。不要把上面的三条单机命令当作一组客户端命令执行。请按照
[远程推理指南](docs/REMOTE_INFERENCE_zh.md)，分别完成两台机器的安装、服务
启动、SSH 隧道和 NaVILA 协议端到端检查。

运行导航前，请打开默认地图 `orcalab_day`，然后选择
**文件 → 打开布局 → `default_set.json`**。NaVILA 使用独立运行环境；
两种方案的 TCP 服务适配器均由本项目提供，模型位于实际执行推理的机器上的
项目默认模型目录。

```bash
./scripts/build_kit.sh
```

该开发包包含 OrcaLab adapter、本地 Go2 task/MJCF/mesh 资源、默认 Go2 运动 checkpoint、全局设置和一个可复现的仓库回合。它不包含 NaVILA/LLaVA 源码、模型权重、3DGS 资源或 IsaacLab。
