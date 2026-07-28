<p align="right"><sub><strong>中文</strong> · <a href="CONDA_SETUP.md">English</a></sub></p>

# 从零配置 Conda 环境

这份指南用于建立与 Orca_VLN 两套运行环境完全匹配的宿主 Conda。
如果新电脑没有 `conda`、Miniconda 与 Anaconda 混装，或安装后运行环境
对不上，请从这里开始。

## 一、先明确环境约定

宿主机只选择一种 Conda 发行版：

- 已有 Miniconda **或** Anaconda 且 `conda info` 正常时，直接保留。
- 全新电脑建议使用体积更小的 Miniconda。
- 不要把 Miniconda 安装到 Anaconda 上，也不要把两者的 `bin` 同时加入
  `PATH`。
- 不要把 OrcaLab 或 NaVILA 安装进 `base`。

项目安装器会在仓库内部创建两套路径环境：

| 路径 | Python | 用途 |
| --- | --- | --- |
| `.conda/envs/orcalab` | 3.12 | OrcaLab、MJLab、Go2 控制与项目测试 |
| `.conda/envs/navila` | 3.10 | NaVILA 推理、PyTorch 2.3 与 FlashAttention |

后续启动脚本会直接调用这两个解释器，运行案例时不需要激活任何环境。

## 二、先检查宿主机

使用 x86-64 的 Ubuntu 22.04 或 24.04。安装 Python 包之前先确认架构和
NVIDIA 宿主驱动：

```bash
uname -m
nvidia-smi
```

只有架构显示 `x86_64` 且 `nvidia-smi` 能列出显卡时才继续。如果出现
NVML driver/library mismatch，请重启电脑一次，不要删除项目环境。

## 三、检查已有 Conda

打开一个新的 Bash 终端：

```bash
type -a conda
conda --version
conda info --base
```

应当只使用一个明确的 base 目录，通常是 `~/miniconda3` 或
`~/anaconda3`。如果以上命令成功，可直接跳到第五节。

如果已经安装但提示找不到 `conda`，初始化准备保留的安装：

```bash
~/miniconda3/bin/conda init bash
source ~/.bashrc
```

已有 Anaconda 时，将命令中的 `miniconda3` 改为 `anaconda3`。
如果 `type -a conda` 同时显示两套发行版，请打开干净终端，并只加载
选定的一套：

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda info --base
```

在确认旧环境归属前，不要直接删除任何一个安装目录。

## 四、全新电脑安装 Miniconda

第三节已经成功时跳过本节。按照
[Miniconda 官方 Linux 指南](https://www.anaconda.com/docs/getting-started/miniconda/install/linux-install)
下载当前安装器：

```bash
cd /tmp
curl -LO https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
sha256sum Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh
```

运行安装器前，将 SHA-256 与
[官方安装器目录](https://repo.anaconda.com/miniconda/)公布的值进行比较。
安装位置使用默认的 `~/miniconda3`，询问是否初始化 Conda 时选择 `yes`。
完成后重新打开终端，或者执行：

```bash
source ~/.bashrc
conda info --base
```

项目同样支持 Anaconda Distribution。如果选择 Anaconda，请遵循
[Anaconda 官方 Linux 安装指南](https://www.anaconda.com/docs/getting-started/anaconda/install/linux-install)，
不要再额外安装 Miniconda。

## 五、验证 Conda 能正常创建环境

下载大模型前先检查 channel：

```bash
conda search python=3.12
```

当前 Anaconda channel 可能首次要求确认服务条款。请先阅读，并仅在符合
自身使用条件时接受。官方显式确认命令为：

```bash
conda tos accept
```

公司代理或防火墙环境应联系管理员放行 Anaconda 官方仓库。不要用
`ssl_verify: false` 绕过证书错误。

## 六、从零安装 Orca_VLN

```bash
git clone https://github.com/openverse-orca/Orca_VLN.git
cd Orca_VLN
```

创建两套隔离环境并下载经过验证的模型：

```bash
./NaVILA-Orca/scripts/setup_all.sh
```

安装支持断点后重跑。网络中断时直接再次执行同一命令，不要手工创建同名
环境替代项目路径环境。

单独验收：

```bash
./NaVILA-Orca/scripts/doctor.sh
```

最后一行必须是：

```text
Orca_VLN installation is ready.
```

检查两个解释器与后续运行要求一致：

```bash
.conda/envs/orcalab/bin/python --version
.conda/envs/navila/bin/python --version
```

第一条必须显示 Python 3.12，第二条必须显示 Python 3.10。

## 七、无需激活，直接运行

不要执行 `conda activate orcalab` 或 `conda activate navila`。在仓库根目录
打开三个终端：

```bash
./NaVILA-Orca/scripts/start_orcalab_gui.sh
```

```bash
./NaVILA-Orca/scripts/start_navvlm_server.sh
```

```bash
./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh
```

终端提示符中存在 `(base)` 没有影响：每个启动脚本都会根据自身路径找到
项目内部的正确解释器。

## 八、故障对应表

| 现象 | 正确处理 |
| --- | --- |
| `conda: command not found` | 对选定安装执行 `conda init bash`，再重新打开 Bash |
| `type -a conda` 同时显示 Miniconda 与 Anaconda | 只选择一套，并只 source 它的 `etc/profile.d/conda.sh` |
| Conda 要求确认 channel ToS | 阅读提示；确认接受时执行 `conda tos accept` |
| `HTTP 000 CONNECTION FAILED` | 联系管理员修复代理/防火墙，保持 TLS 验证开启 |
| 安装中断 | 重新运行 `setup_all.sh`，它会修复相同的项目路径环境 |
| NVML driver/library mismatch | 重启一次，确认 `nvidia-smi` 后重跑安装 |
| Doctor 报 Python 或包版本错误 | 运行对应的 `setup_orcalab_env.sh` 或 `setup_navila_env.sh` 修复 |

参考：[官方 `conda init` 文档](https://docs.conda.io/projects/conda/en/stable/commands/init.html)。
