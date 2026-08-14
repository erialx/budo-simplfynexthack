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
  <a href="#remote-inference">🖥️ 远程推理</a> ·
  <a href="#managed-access">☁️ 托管访问</a> ·
  <a href="#competition-baseline">🏁 竞赛基线</a> ·
  <a href="NaVILA-Orca/docs/GETTING_STARTED_zh.md">📚 文档</a>
</p>

<p align="center">
  <img src="NaVILA-Orca/assets/presentation/factory-overview-two-column.png" alt="带 Go2 机器人的 OrcaLab 工厂导航场景" width="48%" />
  <img src="NaVILA-Orca/assets/presentation/factory-live-monitor.png" alt="Orca_VLN 工厂场景实时监视器" width="48%" />
</p>

> **Orca_VLN 是一套可直接运行的 VLN 基线，可在此基础上针对具体任务继续微调。**
> NaVILA 读取自然语言指令和一段第一视角 RGB 观测，给出下一步导航动作；OrcaLab 执行该动作并返回新的视觉观测。

```text
指令 + 第一视角 RGB  →  NaVILA  →  导航动作  →  OrcaLab  →  下一帧第一视角 RGB
```

本仓库包含示例在 OrcaLab 中运行所需的部分：持续输出第一视角观测、管理场景生命周期、提供预置工厂任务与可运行的控制基线，并保存可追溯的运行记录。NaVILA 保持在独立环境中，通过 TCP 连接。

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

如果 `conda --version` 无法运行，先安装一套干净的 Miniconda，再克隆项目。
方案 B 还要求在两台机器上分别执行下面两个步骤——两端都需要 Git、Conda、
可通过 `nvidia-smi` 检查的 NVIDIA 驱动，以及同一版本的仓库 checkout：

```bash
curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh \
  -o /tmp/miniconda.sh
bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda init bash
conda --version
```

```bash
git clone https://github.com/openverse-orca/Orca_VLN.git
cd Orca_VLN
```

然后选择一种部署方式：

| 部署方案 | 部署结构 | 适用情况 |
| --- | --- | --- |
| **💻 方案 A（默认）— 单机部署** | OrcaLab 与 NaVILA 在同一台机器 | 单机 GPU 资源足够，希望最快完成安装 |
| **🖥️ 方案 B — 远程推理部署（分离部署）** | OrcaLab 在客户端，NaVILA 在独立 GPU 服务器 | 希望推理独立运行，或需要分担 GPU 显存与算力 |
| **☁️ 方案 C — 托管远程推理（AWS SSM）** | OrcaLab 在参与者本机，NaVILA 在主办方托管的 AWS 实例 | 黑客松或实验室场景，参与者使用共享 GPU 服务器但无需拥有它 |

#### 💻 方案 A（默认）— 单机部署

创建两套锁定环境、下载经过验证的 NaVILA 模型，并检查安装结果（最后一行
必须是 `Orca_VLN installation is ready.`）：

```bash
./NaVILA-Orca/scripts/setup_all.sh
```

全新 Ubuntu 首次安装时，脚本可能请求一次 `sudo`，用于安装 OrcaLab GUI
所需的 Qt/XCB 系统库。

```bash
./NaVILA-Orca/scripts/doctor.sh
```

两套环境都位于当前 checkout 的 `.conda/envs/`（OrcaLab 使用 Python 3.12，
NaVILA 使用 Python 3.10）；启动器会自动定位环境，因此不需要设置
`ORCA_VLN_ROOT`，也不用手动执行 `conda activate`。

#### 🖥️ 方案 B — 远程推理部署（分离部署）

客户端只安装 OrcaLab 环境，推理服务器只安装 NaVILA 环境，不要在两台机器
都运行 `setup_all.sh`。请按照独立的[远程推理部署章节](#remote-inference)
完成两端安装、SSH 隧道和端到端验证。支持 Blackwell RTX 5090 Laptop GPU。

#### ☁️ 方案 C — 托管远程推理（AWS SSM）

NaVILA 服务由主办方托管和运维，你无需安装或接触推理服务器。只需在本机
安装 OrcaLab 侧环境，并按[托管访问章节](#managed-access)连接托管服务器：
无需 SSH、无需密钥，一条 AWS SSM 端口转发即可让 NaVILA 表现为
`127.0.0.1:54321` 上的本地服务。

### 💻 方案 A：按步骤 1 → 2 → 3 运行

单机部署使用三个本机终端，并按下面的顺序执行。

<a id="scene-setup"></a>

#### 步骤 1 — 打开 OrcaLab 并组成预设场景

```bash
./NaVILA-Orca/scripts/start_orcalab_gui.sh
```

此时不要运行导航。在 OrcaLab 中：

1. 订阅 `VLN_Presentation` 和 `unitree_robots`。
2. 选择 `VLN_Presentation`；待两个订阅完成后，通过 **文件 → 打开布局** 载入
   [`NaVILA-Orca/factory.json`](NaVILA-Orca/factory.json)。
3. 确认 Go2、红色垃圾桶、蓝色油桶、红色灭火器和白色工业机械臂均已出现。

`VLN_Presentation` 提供工厂，`factory.json` 加载已编排的路线。完成后保持终端 1 和
OrcaLab 运行。

**已经在使用 OrcaLab？** 可以跳过终端 1，直接使用自己已打开的兼容
OrcaLab GUI（本基线验证版本为 OrcaLab 26.7.1）。只需在该 GUI 中选择
`VLN_Presentation`，并载入同一个 `factory.json` 布局文件。

#### 步骤 2 — 在本机启动 NaVILA 服务

```bash
./NaVILA-Orca/scripts/start_navvlm_server.sh
```

等待终端 2 显示正在监听 `127.0.0.1:54321`。

<a id="run-navigation"></a>

#### 步骤 3 — 启动闭环导航

只有 OrcaLab 中已显示完整预设场景、步骤 2 中服务已开始监听后，才能运行
步骤 3。
先在 OrcaLab GUI 中依次选择：**运行 → 开始模拟 → 无仿真程序 → 启动**，
等待外部仿真开始运行。终端 3 只连接这个已启动的会话，不会自行打开
OrcaLab 或启动仿真：

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh
```

将你的导航指令写入
[`NaVILA-Orca/prompts/orcalab_scene_locomotion.txt`](NaVILA-Orca/prompts/orcalab_scene_locomotion.txt)，
或直接使用 `--instruction` 传入，例如：

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh \
  --instruction "Walk toward the red waste bin and pass close by it without stopping. Continue toward the blue barrels and pass them. Then turn right and follow the open aisle beside the white safety fence toward the red fire extinguisher. Keep outside the fenced work cell and avoid the boxes. When the white industrial robotic arm mounted on a gray pedestal is visible, approach the open floor directly in front of the pedestal. Stop about 1.5 meters away from the arm."
```

<a id="remote-inference"></a>

## 🖥️ 方案 B — 远程推理部署

方案 B 只把 NaVILA 推理放到独立 GPU 服务器；OrcaLab GUI、场景、相机、
低层控制和导航循环仍在 OrcaLab 客户端运行。两台机器通过 SSH 本地端口
转发连接，不直接暴露 NaVILA 的 TCP 端口：

```text
OrcaLab 客户端导航 → 127.0.0.1:54321 → SSH 隧道
                  → 推理服务器 127.0.0.1:54321 → NaVILA
```

完整顺序是：**分别安装 → 启动远端服务 → 建立隧道 → 端到端检查 →
准备场景并运行导航 → 清理**。开发包中还提供一份可独立阅读的
[远程推理部署指南](NaVILA-Orca/docs/REMOTE_INFERENCE_zh.md)。

### 分别安装客户端和推理服务器

两台机器都需要 Git、Conda、可通过 `nvidia-smi` 检查的 NVIDIA 驱动，以及
同一版本的 Orca_VLN checkout。不要在两台机器都运行 `setup_all.sh`。

在 **OrcaLab 客户端** 的仓库目录中只安装 OrcaLab 侧依赖：

```bash
./NaVILA-Orca/scripts/check_nvidia_driver.sh
./NaVILA-Orca/scripts/setup_system_deps.sh
./NaVILA-Orca/scripts/setup_orcalab_env.sh
```

在 **推理服务器** 的仓库目录中只安装 NaVILA 侧依赖和模型：

```bash
./NaVILA-Orca/scripts/check_nvidia_driver.sh
./NaVILA-Orca/scripts/setup_navila_env.sh
./NaVILA-Orca/scripts/download_navila_model.sh
```

`doctor.sh` 会检查同一台机器上的两套环境，因此只用于方案 A。上面的分项
安装脚本会分别验证各自的环境和模型。

### 推理服务器：启动 NaVILA

在推理服务器上执行，并保持该终端运行：

```bash
ORCA_VLN_DIR="/path/to/Orca_VLN"  # 改为推理服务器上的实际仓库路径
REMOTE_VLM_PORT="54321"

cd "$ORCA_VLN_DIR"
NAVVLM_HOST="127.0.0.1" \
NAVVLM_PORT="$REMOTE_VLM_PORT" \
./NaVILA-Orca/scripts/start_navvlm_server.sh
```

保持 `NAVVLM_HOST="127.0.0.1"`。不要在安全组或防火墙中开放 `54321`；
NaVILA TCP 服务本身没有 TLS 和身份认证，对外只需开放 SSH 端口。

### OrcaLab 客户端：建立 SSH 隧道

在客户端设置连接参数。以下 IP、用户名、端口均为占位值：

```bash
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

SSH_HOST="xx.xx.xx.xx"             # 改为推理服务器的实际 IP
SSH_USER="your-ssh-user"          # 改为远端账号
SSH_PORT="22"
LOCAL_VLM_PORT="54321"
REMOTE_VLM_PORT="54321"
SSH_CONTROL_SOCKET="${HOME}/.ssh/orca-vln-%C"
```

使用下面的主命令。这里不强制指定密码或密钥：OpenSSH 会根据客户端配置、
SSH agent 和服务端策略自动协商认证方式；如果此前认证方式均未成功，且
客户端与服务端都允许交互式密码认证，SSH 会提示输入账号密码。认证和端口
转发成功后，进程才转入后台。

```bash
ssh -p "$SSH_PORT" \
  -M -S "$SSH_CONTROL_SOCKET" \
  -f -N -T \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L "127.0.0.1:${LOCAL_VLM_PORT}:127.0.0.1:${REMOTE_VLM_PORT}" \
  "${SSH_USER}@${SSH_HOST}"
```

> **仅当你有需要显式指定的 PEM 私钥时，才执行以下内容。没有 PEM 私钥请
> 忽略本段，直接使用上面的主命令。**

先设置 PEM 私钥路径和权限：

```bash
SSH_KEY_PATH="/path/to/private-key.pem"
chmod 600 "$SSH_KEY_PATH"
```

仅在使用该 PEM 私钥时，才将标有 `+` 的两行插入主命令的 `ssh -p` 与 `-M`
之间；其余隧道参数保持不变：

```diff
ssh -p "$SSH_PORT" \
+  -i "$SSH_KEY_PATH" \
+  -o IdentitiesOnly=yes \
  -M -S "$SSH_CONTROL_SOCKET" \
```

### OrcaLab 客户端：执行 SSH 隧道与 NaVILA 协议端到端检查

在启动导航前，从客户端经本地转发端口发送 NaVILA 协议 health 请求：

```bash
./NaVILA-Orca/scripts/check_navvlm_endpoint.py \
  --host 127.0.0.1 \
  --port "$LOCAL_VLM_PORT"
```

检查只有在收到远端 NaVILA 服务的匹配协议响应后才成功；它覆盖
**本地端口 → SSH 隧道 → 远端端口 → NaVILA 应用层**，且不会执行模型推理。
`ssh -O check` 或 `ss` 只能分别检查 SSH master 或本地监听，不能替代这项
端到端检查。服务当前为串行处理；请在导航开始前检查，运行中超时也可能表示
服务正在处理推理请求。

### OrcaLab 客户端：运行导航并清理

先完成方案 A 的[步骤 1：准备场景](#scene-setup)，在 OrcaLab 中启动外部
仿真，然后运行：

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh \
  --vlm-host 127.0.0.1 \
  --vlm-port "$LOCAL_VLM_PORT"
```

导航结束后，只关闭本项目创建的 SSH master：

```bash
ssh -p "$SSH_PORT" \
  -S "$SSH_CONTROL_SOCKET" \
  -O exit \
  "${SSH_USER}@${SSH_HOST}"
```

最后在推理服务器终端按 `Ctrl+C` 停止 NaVILA。若隧道提示
`Address already in use`，请改用空闲的 `LOCAL_VLM_PORT`；若端到端检查失败，
依次确认远端服务仍在运行、两端 `REMOTE_VLM_PORT` 一致，以及 SSH 隧道未断开。

<a id="managed-access"></a>

## ☁️ 方案 C — 托管远程推理（AWS SSM）

方案 C 适用于由主办方托管和运维推理服务器的场景（例如黑客松或实验室，
参与者无需拥有推理主机即可使用 NaVILA）。整个过程不需要 SSH、公网端点
或密钥分发。每位参与者使用 IAM Identity Center（SSO）用户身份认证，该
身份的权限只允许一个动作：对 NaVILA 实例建立 AWS SSM 端口转发。于是推理
服务就表现为 `127.0.0.1:54321` 上的本地服务：

```text
OrcaLab 客户端导航 → 127.0.0.1:54321 → AWS SSM 端口转发
                  → 推理服务器 127.0.0.1:54321 → NaVILA
```

隧道只传输 NaVILA 协议数据，无法在实例上打开 shell。请按照专门的
[托管访问指南](NaVILA-Orca/docs/ACCESS_GUIDE_zh.md)操作：涵盖两个必要组件
的安装（AWS CLI v2 与 Session Manager 插件）、从访问门户获取临时凭据、
建立隧道、无需第三方库的健康检查，以及可选的模拟推理往返。主办方为
每支队伍开通一个 SSO 账号；指南中的固定值为当前测试环境配置。

完成 SSM 连接并通过 NaVILA 服务健康检查后，再完成接下来的步骤。

#### 步骤 1 — 打开 OrcaLab 并组成预设场景

```bash
./NaVILA-Orca/scripts/start_orcalab_gui.sh
```

在 OrcaLab 中：

1. 订阅 `VLN_Presentation` 和 `unitree_robots`。
2. 选择 `VLN_Presentation`；待两个订阅完成后，通过 **文件 → 打开布局** 载入
   [`NaVILA-Orca/factory.json`](NaVILA-Orca/factory.json)。
3. 确认 Go2、红色垃圾桶、蓝色油桶、红色灭火器和白色工业机械臂均已出现。

`VLN_Presentation` 提供工厂，`factory.json` 加载已编排的路线。完成后保持终端 1 和
OrcaLab 运行。

**已经在使用 OrcaLab？** 可以跳过终端 1，直接使用自己已打开的兼容
OrcaLab GUI（本基线验证版本为 OrcaLab 26.7.1）。只需在该 GUI 中选择
`VLN_Presentation`，并载入同一个 `factory.json` 布局文件。

#### 步骤 2 — 启动闭环导航

只有 OrcaLab 中已显示完整预设场景，并已按照托管访问指南建立 SSM 端口转发、
通过 NaVILA 服务健康检查后，才能运行步骤 2。先在 OrcaLab GUI 中依次选择：
**运行 → 开始模拟 → 无仿真程序 → 启动**，等待外部仿真开始运行。终端 2
只连接这个已启动的会话，不会自行打开 OrcaLab 或启动仿真：

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh
```

将你的导航指令写入
[`NaVILA-Orca/prompts/orcalab_scene_locomotion.txt`](NaVILA-Orca/prompts/orcalab_scene_locomotion.txt)，
或直接使用 `--instruction` 传入，例如：

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh \
  --instruction "Walk toward the red waste bin and pass close by it without stopping. Continue toward the blue barrels and pass them. Then turn right and follow the open aisle beside the white safety fence toward the red fire extinguisher. Keep outside the fenced work cell and avoid the boxes. When the white industrial robotic arm mounted on a gray pedestal is visible, approach the open floor directly in front of the pedestal. Stop about 1.5 meters away from the arm."
```

<a id="competition-baseline"></a>

## 🏁 竞赛基线

默认任务要求机器人经过红色垃圾桶和蓝色油桶，沿白色安全围栏右转前往红色灭火器，最后在白色工业机械臂前停止。这条完整闭环的每一步都可直接观察：指令、NaVILA 响应、实际执行的动作、第一视角相机帧和保存的测量结果。

| 评测维度 | 可优化方向 | 基线状态 |
| --- | --- | --- |
| **高层 VLN** | Prompt、任务逻辑、巡检行为、SFT/LoRA | NaVILA 动作闭环可直接运行 |
| **低层控制** | 命令跟踪、转向、停止、稳定性、恢复 | 提供的控制模型刻意保持通用，未针对导航调优 |
| **系统与证据** | 场景配置、相机采集、动作轨迹、可复现性 | 运行记录自动保存 |

提供的控制模型是保守的平地基线，并未针对当前工厂场景、NaVILA 的离散动作片段或特定任务的停车精度进行调优。这一差距是有意保留的：低层执行质量本身就是竞赛指标，而不是需要被隐藏的实现细节。

## 🧩 进阶方向

- [快速上手](NaVILA-Orca/docs/GETTING_STARTED_zh.md) — 场景配置、进程、相机与首次运行。
- [远程推理](NaVILA-Orca/docs/REMOTE_INFERENCE_zh.md) — 通过 SSH 隧道的分离部署。
- [托管推理访问](NaVILA-Orca/docs/ACCESS_GUIDE_zh.md) — 经 AWS SSM 端口转发访问托管 NaVILA 服务器，无需 SSH 或密钥。
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
