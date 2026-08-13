<p align="right"><sub><a href="ACCESS_GUIDE.md">English</a> · <strong>中文</strong></sub></p>

# NaVILA 远程推理 — 访问与冒烟测试指南

本文档面向**单个测试人员**，帮助你实际连上 NaVILA GPU 推理端点。文档中的
参数固定为当前环境（单实例、单账号、东京区域），并假定使用 **Linux** 客户端
（以下命令基于 Debian/Ubuntu）。本文档**不是**学生讲义，请配合现场讲解
使用。

你的身份是 IAM Identity Center（SSO）用户，该身份只被允许**做一件事：
对 NaVILA 实例建立 SSM 端口转发**。没有 shell 权限，也没有任何其他 AWS
访问权限。隧道建立后，推理服务就表现为 `127.0.0.1:54321` 上的本地服务。

连接前只需安装**两个组件**（步骤 1–2）。认证只需从 AWS 访问门户复制粘贴
一组临时凭据（步骤 3）。uv 是第三项**可选**安装，仅用于步骤 6 的模拟推理，
health 检查不需要。

## 本环境固定参数

| | |
|---|---|
| AWS 账号 | `sn.devlabs` (`433129444392`) |
| 区域 | `ap-northeast-1`（东京） |
| 实例 | `i-0d3b20f83a073d940` |
| 访问门户（SSO）URL | `https://d-9667b91afb.awsapps.com/start` |
| 角色 | `NavilaPortForwardOnly` |
| 本地端口 | `54321` |

> **有一件事不受你控制：** 实例上的 NaVILA **服务必须处于运行状态**。你
> 无法自行启动它——按设计，你的权限只包含端口转发。如果步骤 5 的 health
> 检查连不上，那是管理员需要处理的事项。

## 你将用到两个终端

- **终端 A** — 你的 AWS 凭据 + SSM 隧道。全程保持打开。
- **终端 B** — health 检查（以及可选的模拟推理）。只访问
  `127.0.0.1:54321`，因此**不需要任何 AWS 凭据**。

---

## 1. 安装 AWS CLI v2

```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
aws --version
```

（ARM64 机器请把 URL 换成 `awscli-exe-linux-aarch64.zip`。）

## 2. 安装 Session Manager 插件

端口转发实际由该插件完成，CLI 会调用它。

```bash
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o "session-manager-plugin.deb"
sudo dpkg -i session-manager-plugin.deb
session-manager-plugin
```

最后一条命令应输出一行 “successfully installed”。（RPM 系发行版使用同一
S3 路径下 `linux_64bit/` 目录中的 `.rpm` 包。）

## 3. 获取临时凭据 — 终端 A

1. 在浏览器中打开访问门户：**https://d-9667b91afb.awsapps.com/start**，
   使用你的用户名和密码登录。
2. 选择账号 **`sn.devlabs`**，再选择角色 **`NavilaPortForwardOnly`**。
3. 点击 **Access keys**。
4. 在 **Option 1: Set AWS environment variables** 下方复制整段内容。它
   长这样（你的会是真实值）：

   ```bash
   export AWS_ACCESS_KEY_ID="ASIA..."
   export AWS_SECRET_ACCESS_KEY="..."
   export AWS_SESSION_TOKEN="..."
   ```

5. 粘贴到**终端 A**。确认已生效——不需要 `--profile`：

   ```bash
   aws sts get-caller-identity
   ```

   输出中应出现一段形如
   `assumed-role/AWSReservedSSO_NavilaPortForwardOnly_.../<你的用户名>` 的 ARN。

> 这些凭据是**临时的**，只存在于当前终端。过期后（或新开终端时），重复
> 步骤 3 从门户获取新的一组。如果想用一条命令完成刷新，参见附录中基于
> profile 的替代方案。

---

## 4. 建立隧道 — 终端 A

使用刚才粘贴的凭据，在同一个终端执行：

```bash
aws ssm start-session \
  --target i-0d3b20f83a073d940 \
  --region ap-northeast-1 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["54321"],"localPortNumber":["54321"]}'
```

等待出现：

```text
Waiting for connections...
```

**保持这个终端打开。** 关闭它（或按 Ctrl-C）会断开隧道。如果这里报
`AccessDeniedException`（凭据过期或无效），重做步骤 3 刷新凭据。

---

## 5. Health 检查 — 终端 B

将以下内容保存为 `check_navvlm_endpoint.py`。它向 NaVILA 发送 health 请求
（8 字节长度前缀 + JSON，不带图片、不做推理）并校验响应。脚本只使用标准库，
因此用系统自带的 `python3` 运行即可，且不需要 AWS 凭据。

```python
#!/usr/bin/env python3
"""Health-check the NaVILA endpoint (no inference). Standard library only."""

import json
import socket
import sys

HOST, PORT = "127.0.0.1", 54321


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise RuntimeError(f"connection closed after {len(buf)} of {n} bytes")
        buf += chunk
    return buf


req = json.dumps({"type": "health"}).encode()
with socket.create_connection((HOST, PORT), timeout=5) as sock:
    sock.sendall(len(req).to_bytes(8, "big"))
    sock.sendall(req)
    size = int.from_bytes(recv_exact(sock, 8), "big")
    resp = json.loads(recv_exact(sock, size).decode())

if resp.get("service") == "navila-vlm" and resp.get("status") == "ok":
    print(f"NaVILA endpoint healthy at {HOST}:{PORT} "
          f"(protocol_version={resp.get('protocol_version')})")
else:
    print(f"unexpected health response: {resp}", file=sys.stderr)
    sys.exit(2)
```

运行：

```bash
python3 check_navvlm_endpoint.py
```

预期输出：

```text
NaVILA endpoint healthy at 127.0.0.1:54321 (protocol_version=1)
```

如果报 `connection closed` 或 `connection refused`，说明隧道已建立，但实例
上的 **NaVILA 服务没有运行**——这属于管理员操作，你的权限无法修复。请
联系管理员启动服务，然后重试。

**这就是核心冒烟测试。** 步骤 6 仅在你需要确认真实推理往返时执行。

---

## 6. （可选）模拟推理 — 终端 B

这一步比 health 检查更进一步：发送 8 帧真实图像并在 GPU 上运行模型。
它需要 `pillow` 库，因此这是唯一需要用到包管理器的步骤。
[uv](https://docs.astral.sh/uv/) 最简单——它会读取 `# /// script` 头并自动
安装 `pillow`，无需手动创建 venv。

安装 uv（已安装可跳过），然后新开一个 shell 使其出现在 `PATH` 上：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

将以下内容保存为 `mock_infer.py`：

```python
# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow"]
# ///
"""Send one real mock inference to the NaVILA server over the raw protocol."""

import base64
import io
import json
import os
import socket
import sys
import time

from PIL import Image

HOST, PORT = "127.0.0.1", 54321
NUM_FRAMES = 8
QUERY = "Walk forward down the corridor and stop at the open door."


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise RuntimeError(f"connection closed after {len(buf)} of {n} bytes")
        buf += chunk
    return buf


def make_frame():
    # Random noise is fine — this is a plumbing/latency smoke test, not a
    # correctness test of the navigation output.
    img = Image.frombytes("RGB", (512, 512), os.urandom(512 * 512 * 3))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")


images = [make_frame() for _ in range(NUM_FRAMES)]
payload = json.dumps({"images": images, "query": QUERY}).encode()
print(f"query   : {QUERY!r}")
print(f"frames  : {NUM_FRAMES}  payload: {len(payload) / 1024:.1f} KiB")

t0 = time.perf_counter()
with socket.create_connection((HOST, PORT), timeout=120) as sock:
    sock.sendall(len(payload).to_bytes(8, "big"))
    sock.sendall(payload)
    size = int.from_bytes(recv_exact(sock, 8), "big")
    resp = json.loads(recv_exact(sock, size).decode())
elapsed = time.perf_counter() - t0

print(f"latency : {elapsed:.3f} s")
print(f"response: {resp!r}")
```

运行：

```bash
uv run mock_infer.py
```

预期——大约 **2.7 s** 并返回一个动作字符串（具体措辞可能不同）：

```text
query   : 'Walk forward down the corridor and stop at the open door.'
frames  : 8  payload: ~630 KiB
latency : 2.68 s
response: 'The next action is move forward 25 cm.'
```

这次端到端往返——你的受限 SSO 身份 → SSM 隧道 → GPU 推理 → 返回动作——
就完成了完整的测试。

---

## 7. 结束后

在**终端 A** 按 Ctrl-C 关闭隧道。AWS 侧无需任何清理。

## 故障排查

| 症状 | 原因 | 处理 |
|---|---|---|
| 报 `AccessDeniedException`，提示凭据过期或无效 | 临时凭据超时（或粘贴到了错误的终端） | 重做步骤 3——从门户获取新的环境变量并粘贴到终端 A |
| `Unable to locate credentials` | 当前终端没有凭据 | 你开错了终端，或还没有粘贴步骤 3 的内容 |
| 报 `AccessDeniedException` 且包含 `SSM-SessionManagerRunShell` | 你尝试了普通 shell 会话（未传 `--document-name`） | 这是设计上禁止的——始终像步骤 4 那样传入端口转发文档 |
| Health 检查：`connection refused` / `connection closed` | 实例上的 NaVILA 服务未运行 | 请管理员启动（你无法操作——只有端口转发权限） |
| Health 检查：`connection refused` 且隧道终端已关闭 | 隧道未运行 | 重新打开终端 A（步骤 3–4） |
| 建立隧道时报 `Address already in use` | 本地 54321 端口被旧隧道占用 | 关闭旧隧道，或将隧道的 `localPortNumber` **与**两个脚本中的 `PORT` 常量一起改成空闲端口 |
| `session-manager-plugin: command not found` | 插件未安装 | 重做步骤 2 |
| `uv: command not found` | 新 shell 尚未加载 `uv` | 新开终端或执行 `source ~/.bashrc` |

---

## 附录 — 备选认证：持久 SSO profile

不想每次会话都复制粘贴临时环境变量（步骤 3），可以一次性配置命名
profile，之后一条命令即可刷新。权衡在于：现在做一次向导配置，之后凭据
过期时就不必再打开浏览器。

一次性配置：

```bash
aws configure sso
```

```text
SSO session name (Recommended): navila-sso
SSO start URL [None]:            https://d-9667b91afb.awsapps.com/start
SSO region [None]:               ap-northeast-1
SSO registration scopes [sso:account:access]:   (press Enter)
(choose account)   sn.devlabs (433129444392)
(role)             NavilaPortForwardOnly
CLI default client Region [None]:   ap-northeast-1
CLI default output format [None]:   json
CLI profile name [...]:             navila
```

之后每次需要访问时，登录并在 AWS 命令中加上 `--profile navila`：

```bash
aws sso login --profile navila
aws sts get-caller-identity --profile navila

aws ssm start-session \
  --target i-0d3b20f83a073d940 \
  --profile navila --region ap-northeast-1 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["54321"],"localPortNumber":["54321"]}'
```

health 检查和模拟推理不变——它们从不使用 AWS 凭据。
