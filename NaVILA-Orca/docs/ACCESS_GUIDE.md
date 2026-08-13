# NaVILA remote inference — access & smoke-test guide

A companion for a **single tester** getting hands-on access to the NaVILA GPU
inference endpoint. It is intentionally hardcoded to the current setup (one
instance, one account, Tokyo) and assumes a **Linux** client (Debian/Ubuntu
commands shown). It is *not* the student handout — expect a live walkthrough
alongside it.

You authenticate as an IAM Identity Center (SSO) user whose permissions allow
**exactly one thing: an SSM port-forward to the NaVILA instance.** No shell, no
other AWS access. Once the tunnel is up, the inference server looks like a local
service on `127.0.0.1:54321`.

Only **two installs** are needed before you can connect (steps 1–2). Auth is just
copy-pasting temporary credentials from the AWS access portal (step 3). uv is a
third, **optional** install — only for the mock inference in step 6, not the
health check.

## Fixed values for this setup

| | |
|---|---|
| AWS account | `sn.devlabs` (`433129444392`) |
| Region | `ap-northeast-1` (Tokyo) |
| Instance | `i-0d3b20f83a073d940` |
| Access portal (SSO) URL | `https://d-9667b91afb.awsapps.com/start` |
| Role | `NavilaPortForwardOnly` |
| Local port | `54321` |

> **One thing outside your control:** the NaVILA **server must be running** on the
> instance. You cannot start it yourself — your access is port-forward only by
> design. If the health check in step 5 can't reach it, that's an admin action.

## What you'll end up running

Two terminals:

- **Terminal A** — your AWS credentials + the SSM tunnel. Stays open the whole time.
- **Terminal B** — the health check (and optionally a mock inference). Talks only
  to `127.0.0.1:54321`, so it needs **no AWS credentials**.

---

## 1. Install the AWS CLI v2

```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
aws --version
```

(On ARM64, swap the URL for `awscli-exe-linux-aarch64.zip`.)

## 2. Install the Session Manager plugin

The plugin is what actually carries the port-forward; the CLI shells out to it.

```bash
curl "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb" -o "session-manager-plugin.deb"
sudo dpkg -i session-manager-plugin.deb
session-manager-plugin
```

The last command should print a "successfully installed" line. (RPM-based
distros use the `.rpm` from the same S3 path under `linux_64bit/`.)

## 3. Get temporary credentials — Terminal A

1. Open the access portal in a browser: **https://d-9667b91afb.awsapps.com/start**
   and sign in with your username and password.
2. Choose the account **`sn.devlabs`**, then the role **`NavilaPortForwardOnly`**.
3. Click **Access keys**.
4. Under **Option 1: Set AWS environment variables**, copy the block. It looks
   like this (yours will have real values):

   ```bash
   export AWS_ACCESS_KEY_ID="ASIA..."
   export AWS_SECRET_ACCESS_KEY="..."
   export AWS_SESSION_TOKEN="..."
   ```

5. Paste it into **Terminal A**. Confirm it worked — no `--profile` needed:

   ```bash
   aws sts get-caller-identity
   ```

   You should see an ARN containing
   `assumed-role/AWSReservedSSO_NavilaPortForwardOnly_.../<you>`.

> These credentials are **temporary** and live only in this terminal. When they
> expire (or if you open a new terminal), just repeat step 3 to grab a fresh set
> from the portal. Prefer a one-command refresh instead? See the profile-based
> alternative in the appendix.

---

## 4. Open the tunnel — Terminal A

Same terminal, using the credentials you just pasted:

```bash
aws ssm start-session \
  --target i-0d3b20f83a073d940 \
  --region ap-northeast-1 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["54321"],"localPortNumber":["54321"]}'
```

Wait for:

```text
Waiting for connections...
```

**Leave this terminal open.** Closing it (or Ctrl-C) tears down the tunnel. If
you get an `AccessDeniedException` about expired/invalid credentials here, redo
step 3 to refresh them.

---

## 5. Health check — Terminal B

Save this as `check_navvlm_endpoint.py`. It sends NaVILA's health request
(8-byte length prefix + JSON, no images, no inference) and checks the reply.
Standard library only, so run it with the system `python3` — and no AWS creds.

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

Run it:

```bash
python3 check_navvlm_endpoint.py
```

Expected:

```text
NaVILA endpoint healthy at 127.0.0.1:54321 (protocol_version=1)
```

If instead it errors with `connection closed` or `connection refused`, the tunnel
is up but the **NaVILA server isn't running** on the instance — that's an admin
action, not something your access can fix. Ping your admin to start it, then retry.

**That's the core smoke test.** Step 6 is only if you want to confirm a real
inference round trip.

---

## 6. (Optional) Mock inference — Terminal B

This goes beyond the health check: it sends 8 real frames and runs the model on
the GPU. It needs the `pillow` library, so it's the one place you need a package
manager. [uv](https://docs.astral.sh/uv/) is the easiest — it reads the
`# /// script` header and installs `pillow` automatically, no manual venv.

Install uv (skip if you already have it), then open a new shell so it's on your `PATH`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Save this as `mock_infer.py`:

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

Run it:

```bash
uv run mock_infer.py
```

Expected — roughly **2.7 s** and an action string (the exact wording varies):

```text
query   : 'Walk forward down the corridor and stop at the open door.'
frames  : 8  payload: ~630 KiB
latency : 2.68 s
response: 'The next action is move forward 25 cm.'
```

That end-to-end round trip — your locked-down SSO identity → SSM tunnel → GPU
inference → action back — is the full test.

---

## 7. When you're finished

Ctrl-C **Terminal A** to close the tunnel. Nothing to clean up on the AWS side.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `AccessDeniedException` about expired/invalid credentials | Temporary creds timed out (or wrong terminal) | Redo step 3 — grab fresh env vars from the portal and paste into Terminal A |
| `Unable to locate credentials` | No creds in this terminal | You're in the wrong terminal, or haven't pasted the step-3 block yet |
| `AccessDeniedException` naming `SSM-SessionManagerRunShell` | You tried a plain shell session (no `--document-name`) | That's blocked by design — always pass the port-forward document as in step 4 |
| Health check: `connection refused` / `connection closed` | NaVILA server not running on the instance | Ask admin to start it (you can't — port-forward-only access) |
| Health check: `connection refused` and the tunnel terminal is closed | Tunnel not running | Re-open Terminal A (steps 3–4) |
| `Address already in use` when opening the tunnel | Local port 54321 taken by an old tunnel | Close the old one, or change the tunnel's `localPortNumber` **and** the `PORT` constant in both scripts to a free port |
| `session-manager-plugin: command not found` | Plugin not installed | Redo step 2 |
| `uv: command not found` | New shell hasn't picked up `uv` | Open a new terminal or `source ~/.bashrc` |

---

## Appendix — alternative auth: a persistent SSO profile

Instead of copy-pasting temporary env vars each session (step 3), you can set up
a named profile once and refresh it with a single command. Trade-off: a one-time
wizard now, versus a browser round-trip each time the env-var creds expire.

One-time setup:

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

Then, whenever you need access, log in and add `--profile navila` to the AWS
commands:

```bash
aws sso login --profile navila
aws sts get-caller-identity --profile navila

aws ssm start-session \
  --target i-0d3b20f83a073d940 \
  --profile navila --region ap-northeast-1 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["54321"],"localPortNumber":["54321"]}'
```

The health check and mock inference are unchanged — they never use AWS
credentials.
