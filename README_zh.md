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
  <a href="#competition-baseline">🏁 竞赛基线</a> ·
  <a href="NaVILA-Orca/docs/GETTING_STARTED_zh.md">📚 文档</a>
</p>

<p align="center">
  <img src="NaVILA-Orca/assets/presentation/warehouse-overview.png" alt="OrcaLab 仓库导航场景" width="48%" />
  <img src="NaVILA-Orca/assets/presentation/live-monitor.png" alt="Orca_VLN 实时监视器" width="48%" />
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

单机部署只需准备一台机器；分离部署则要在 OrcaLab 客户端和远端推理服务器
上分别准备 Git、Conda、NVIDIA 驱动和一份相同版本的仓库 checkout。

如果 `conda --version` 无法运行，直接安装一套干净的 Miniconda。分离部署时
在两台机器分别执行：

```bash
curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh \
  -o /tmp/miniconda.sh
bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"
source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda init bash
conda --version
```

克隆项目。分离部署时，在客户端和推理服务器分别执行，并保持相同版本：

```bash
git clone https://github.com/openverse-orca/Orca_VLN.git
cd Orca_VLN
```

根据部署方式选择一种安装方法。

#### 单机部署（默认）

创建两套锁定环境，并下载经过验证的 NaVILA 模型：

```bash
./NaVILA-Orca/scripts/setup_all.sh
```

全新 Ubuntu 首次安装时，脚本可能请求一次 `sudo`，用于安装 OrcaLab GUI
所需的 Qt/XCB 系统库。
脚本也会在首次打开 GUI 前准备好 OrcaLab 官方原生 viewport 和场景 pak，
避免 OrcaLab 在首启过程中临时安装组件并要求重启。

单机部署时，单独检查安装结果。最后一行必须是
`Orca_VLN installation is ready.`：

```bash
./NaVILA-Orca/scripts/doctor.sh
```

单机部署的两套环境都位于当前 checkout 的 `.conda/envs/`：OrcaLab 使用 Python
3.12，NaVILA 使用 Python 3.10。启动器根据自己的文件位置定位环境，
因此不需要设置 `ORCA_VLN_ROOT`，也不用手动执行 `conda activate` 或
`deactivate`。

#### 分离部署

如果准备把 NaVILA 放在独立推理服务器上，不需要在两台机器都执行
`setup_all.sh`。请分别安装所需环境：

```bash
# OrcaLab 客户端
./NaVILA-Orca/scripts/check_nvidia_driver.sh
./NaVILA-Orca/scripts/setup_system_deps.sh
./NaVILA-Orca/scripts/setup_orcalab_env.sh

# 独立推理服务器（在远端仓库目录中执行）
./NaVILA-Orca/scripts/check_nvidia_driver.sh
./NaVILA-Orca/scripts/setup_navila_env.sh
./NaVILA-Orca/scripts/download_navila_model.sh
```

`doctor.sh` 会检查同一台机器上的两套环境，因此只用于默认单机部署；分离部署
使用上面的分项安装。环境安装脚本会在结束前验证自身，模型下载脚本会校验
模型文件，远端服务启动器还会实际检查 CUDA 推理能力。

### 按 A/B/C 顺序运行

单机部署使用三个本机终端。分离部署的 B 位于远端服务器；SSH 隧道会在
本机的终端 C 中转入后台，然后同一终端继续启动导航。

#### A — 打开 OrcaLab 并组成预设场景

```bash
./NaVILA-Orca/scripts/start_orcalab_gui.sh
```

此时不要运行导航。在 OrcaLab 中：

1. 打开默认地图 `orcalab_day`。
2. 选择 **文件 → 打开布局**，选中
   [`NaVILA-Orca/default_set.json`](NaVILA-Orca/default_set.json)。
3. 等待 Go2、蓝色桶、黄色车辆及其他预设对象出现在场景中。

> **资产订阅——默认案例的最低要求。** 在 OrcaLab 中先订阅
> `SimpleMovement_Conveybelt`、`SimpleMovement_Slope`、`unitree_robots` 和
> `OrcaPlaygroundAssets`，再加载 `default_set.json`。它们分别提供布局所需的
> 纸箱、料箱/蓝桶、Go2 和车辆。`IndustrialWarehouse1_3dgs`、
> `IndustrialWarehouse2_3dgs`、`kitchen_3dgs`、
> `AutoProductionLine_Warehouse` 是扩展场景；用户按自己的训练或评测需要订阅。

仅打开地图不会得到预设任务；`default_set.json` 才是实例化这些对象的
布局文件。完成后保持终端 A 和 OrcaLab 运行。

**已经在使用 OrcaLab？** 可以跳过终端 A，直接使用自己已打开的兼容
OrcaLab GUI（本基线验证版本为 OrcaLab 26.6.3）。只需在该 GUI 中打开
`orcalab_day`，并载入同一个布局文件。

#### B — 启动 NaVILA 服务（二选一）

##### 方式 1：在本机启动（默认）

```bash
./NaVILA-Orca/scripts/start_navvlm_server.sh
```

等待终端 B 显示正在监听 `127.0.0.1:54321`。

<a id="remote-inference"></a>

##### 方式 2：在独立 GPU 服务器上启动

这种方式让 OrcaLab 和导航循环留在本机，仅把 NaVILA 推理放到远端 GPU
服务器。远端服务仍绑定回环地址，通过 SSH 加密隧道映射到本机：

```text
本机导航进程 → 127.0.0.1:54321 → SSH 隧道
             → 远端 127.0.0.1:54321 → NaVILA
```

先在远端服务器启动推理服务，并保持该终端运行：

```bash
ORCA_VLN_DIR="/path/to/Orca_VLN"  # 改为远端仓库的实际路径
REMOTE_VLM_PORT="54321"

cd "$ORCA_VLN_DIR"
NAVVLM_HOST="127.0.0.1" \
NAVVLM_PORT="$REMOTE_VLM_PORT" \
./NaVILA-Orca/scripts/start_navvlm_server.sh
```

不要把 `NAVVLM_HOST` 改为 `0.0.0.0`，也不需要在安全组或防火墙中开放
`54321`；此 TCP 服务本身没有 TLS 和身份认证，对外只需开放 SSH 端口。

然后在准备执行步骤 C 的本机终端中设置连接参数。下列值都是示例占位符，
请按实际服务器修改，IP、域名、用户名和端口没有写死：

```bash
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

SSH_HOST="xx.xx.xx.xx"              # 请改为远端服务器的实际 IP
SSH_USER="your-ssh-user"           # 远端账号
SSH_PORT="22"
LOCAL_VLM_PORT="54321"
REMOTE_VLM_PORT="54321"
SSH_CONTROL_SOCKET="${HOME}/.ssh/orca-vln-%C"
```

账号密码登录使用下面的命令。SSH 会在前台交互式询问密码，认证并成功建立
端口转发后才转入后台：

```bash
ssh -p "$SSH_PORT" \
  -M -S "$SSH_CONTROL_SOCKET" \
  -f -N -T \
  -o PreferredAuthentications=password,keyboard-interactive \
  -o PubkeyAuthentication=no \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L "127.0.0.1:${LOCAL_VLM_PORT}:127.0.0.1:${REMOTE_VLM_PORT}" \
  "${SSH_USER}@${SSH_HOST}"
```

首次连接时先向服务器管理员核对 SSH 主机指纹，再接受提示并输入账号密码。
不要把密码写进命令、环境变量或 `sshpass -p`，以免泄露到 shell 历史或进程
列表。如果服务器禁用了密码登录，请使用下面的私钥方式或联系管理员，不要
为了方便而关闭服务端的安全策略。

使用 PEM 或其他 SSH 私钥时，私钥路径同样通过变量配置：

```bash
SSH_KEY_PATH="/path/to/private-key.pem"
chmod 600 "$SSH_KEY_PATH"

ssh -p "$SSH_PORT" \
  -i "$SSH_KEY_PATH" \
  -o IdentitiesOnly=yes \
  -M -S "$SSH_CONTROL_SOCKET" \
  -f -N -T \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L "127.0.0.1:${LOCAL_VLM_PORT}:127.0.0.1:${REMOTE_VLM_PORT}" \
  "${SSH_USER}@${SSH_HOST}"
```

验证 SSH 主连接和本地监听端口：

```bash
ssh -p "$SSH_PORT" \
  -S "$SSH_CONTROL_SOCKET" \
  -O check \
  "${SSH_USER}@${SSH_HOST}"

ss -ltn "sport = :${LOCAL_VLM_PORT}"
```

保持默认本地端口时，直接继续执行下面的终端 C 命令，不需要修改参数。若隧道
报 `Address already in use`，说明本机端口已被占用，可修改 `LOCAL_VLM_PORT`
并把新值传给终端 C；若推理时出现 `Connection refused`，请检查远端服务
是否已启动，以及 `REMOTE_VLM_PORT` 是否一致。

#### C — 启动闭环导航

只有 OrcaLab 中已显示完整预设场景、B 中服务已开始监听后，才能运行 C；
远程方式还必须确认上面的 SSH 主连接和本地监听端口均正常。
先在 OrcaLab GUI 中依次选择：**运行 → 开始模拟 → 无仿真程序 → 启动**，
等待外部仿真开始运行。终端 C 只连接这个已启动的会话，不会自行打开
OrcaLab 或启动仿真：

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh
```

如果远程方式修改了 `LOCAL_VLM_PORT`，在建立隧道的同一终端中改用：

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh \
  --vlm-host 127.0.0.1 \
  --vlm-port "$LOCAL_VLM_PORT"
```

远程导航结束后，只关闭本项目创建的 SSH 主连接：

```bash
ssh -p "$SSH_PORT" \
  -S "$SSH_CONTROL_SOCKET" \
  -O exit \
  "${SSH_USER}@${SSH_HOST}"
```

远端推理服务可在其终端按 `Ctrl+C` 停止。

<a id="competition-baseline"></a>

## 🏁 竞赛基线

默认任务要求机器人接近蓝色桶，并在黄色车辆前停止。这条完整闭环的每一步都可直接观察：指令、NaVILA 响应、实际执行的动作、第一视角相机帧和保存的测量结果。

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

`NaVILA-Orca/` 包含运行时、默认全局设置、仓库任务、机器人资源和基线 checkpoint。使用以下命令构建干净的分发包：

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
