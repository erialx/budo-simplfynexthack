# NaVILA-Orca × OrcaLab × Claude Code Bridge — Team Setup Guide

## What this does

Lets Claude Code give NaVILA/OrcaLab natural-language navigation instructions
directly, instead of you typing `--instruction "..."` into a terminal by hand.
Claude calls a tool; the tool runs the existing, already-working
`run_orcalab_scene_locomotion.sh` pipeline underneath; Claude gets back a
structured result (decisions taken, how it ended, final position) and can
decide what to do next.

**Important: this does not reimplement NaVILA or OrcaLab.** It's a thin
wrapper around the pipeline you already use manually. If the manual pipeline
doesn't work on your machine, the bridge won't either — get the normal
3-terminal setup working first (see Prerequisites).

---

## A note on environments (if this trips you up like it did us)

`conda activate orcalab` is **not** a virtual machine — it's the same
machine, same filesystem, same network. It only changes which Python
interpreter is on `PATH` in that terminal. Whether `orcalab` is active or not
has zero effect on installing/running the Claude Code CLI, which lives
outside any conda env. The only place the `orcalab` env matters is which
Python interpreter actually runs `navila_bridge.py` — which is why the config
below uses a hardcoded absolute path to that interpreter, not whatever's
active in your current shell.

---

## Prerequisites

Before touching the bridge at all, confirm you can already do the normal
manual workflow:

1. AWS SSM tunnel opens successfully (Terminal 1) and forwards NaVILA's VLM
   server to `localhost:54321`.
2. OrcaLab GUI opens (Terminal 2), loads `factory.json`, and you can start
   the simulation.
3. `./NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh --instruction "..."`
   (Terminal 3) runs successfully and the robot moves in the GUI.

If any of these don't already work for you, fix that first — see the
original `OrcaLab_and_NaVILA_set_up` guide.

---

## Part 1 — Get the bridge script

The `navila_bridge.py` file is attached alongside this guide. Save it
somewhere inside your `Orca_VLN` checkout, e.g.:

```bash
cp navila_bridge.py ~/Orca_VLN/NaVILA-Orca/navila_bridge.py
```

## Part 2 — Configure it for your machine

Open the file and check the config block near the top:

```python
ORCA_VLN_ROOT = Path("/home/guest/Orca_VLN")
LOCOMOTION_SCRIPT = ORCA_VLN_ROOT / "NaVILA-Orca" / "scripts" / "run_orcalab_scene_locomotion.sh"
MEASUREMENTS_PATH = ORCA_VLN_ROOT / "NaVILA-Orca" / "outputs" / "scene_locomotion_smoke" / "measurements.json"
```

These paths were correct for one team member's machine, not necessarily
yours. Find your own with:

```bash
echo $HOME          # confirm your home directory
find ~ -maxdepth 2 -iname "Orca_VLN"   # locate your actual checkout
```

Edit `ORCA_VLN_ROOT` to match, then confirm the derived paths are right:

```bash
ls $ORCA_VLN_ROOT/NaVILA-Orca/scripts/run_orcalab_scene_locomotion.sh
```

## Part 3 — Install the MCP SDK

Inside the `orcalab` conda environment specifically (this package needs to be
importable by whatever Python eventually runs the bridge):

```bash
conda activate orcalab
pip install "mcp[cli]"
python -c "import mcp; print(mcp.__file__)"   # confirm it's in the orcalab env's site-packages
```

## Part 4 — Get your 3-terminal setup running

Open the AWS tunnel (Terminal 1) and start the OrcaLab GUI + simulation
(Terminal 2), exactly as normal. Don't run Terminal 3's script directly —
the bridge replaces that.

## Part 5 — Unit-test the bridge logic directly (no MCP, no Claude Code yet)

This isolates "does my bridge logic work" from "does the MCP protocol work."
From inside the directory where you saved the file:

```bash
python3 -c "from navila_bridge import _health_check_impl; print(_health_check_impl())"
```

Expect:
```
{'ok': True, 'response': {'service': 'navila-vlm', 'status': 'ok', 'protocol_version': 1}}
```

If that fails with a connection error, the tunnel isn't actually open — fix
that before continuing.

If it succeeds, run a real episode. **This takes several minutes and prints
nothing until it's completely done** — that's expected, not a hang. Watch
the OrcaLab GUI in parallel; if the dog is moving, it's working:

```bash
python3 -c "
from navila_bridge import _run_instruction_impl
result = _run_instruction_impl('Walk toward the red waste bin and pass close by it without stopping.', 900)
import json
print(json.dumps(result, indent=2, default=str)[:3000])
"
```

Expect `"ok": true`, a `termination_reason` like `"stop"`, and a
`vlm_outputs` list reading like a sequence of movement decisions. If you get
`"ok": false` with nothing else populated, it likely timed out — the episode
genuinely can take several minutes (real GPU inference per decision, ~10-30
decisions per episode), so don't be too aggressive lowering the timeout.

⚠️ **Known open issue**: we hit a `TypeError: Object of type X is not JSON
serializable` once during testing and never fully root-caused it before
moving on (we worked around it with `default=str`, shown above, which masks
rather than fixes it). If you hit this, please dig in and report back what
`X` actually is — check the full traceback, the `default=str` line above
will show you a stringified version of the offending value which should also
help.

## Part 6 — (Optional) Test via the MCP Inspector

```bash
mcp dev navila_bridge.py
```

This opens a local web UI to manually invoke the tools through the real MCP
protocol rather than calling the Python functions directly. **Requires
Node.js 20+.** If you get an error like
`The requested module 'node:util' does not provide an export named 'styleText'`,
your Node version is too old — check with `node --version`, and upgrade via
[nvm](https://github.com/nvm-sh/nvm) if needed:

```bash
nvm install 22 && nvm use 22
```

This step is genuinely optional — skip it if you want to move faster.
Claude Code itself doesn't use this Inspector tool at all; it talks to
`navila_bridge.py` directly.

## Part 7 — Install the Claude Code CLI (if you don't already have it)

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Open a new terminal (or `source ~/.bashrc`) afterward, then confirm:

```bash
which claude
claude --version
```

## Part 8 — Register the bridge with Claude Code

First, find the **exact** path to your `orcalab` conda env's Python — don't
guess or retype from memory:

```bash
conda env list
```

Copy the path shown next to `orcalab` exactly, then confirm the interpreter
exists there:

```bash
ls <that-path>/bin/python
```

Then, from the same directory where `navila_bridge.py` lives:

```bash
claude mcp add navila-orcalab -- <that-path>/bin/python /full/path/to/navila_bridge.py
```

Verify it registered:

```bash
claude mcp list
```

You want `navila-orcalab` listed as connected, not failed. (Registration is
scoped to the directory you ran this from — you'll need to be in that same
directory, or a subdirectory of it, when you later start `claude`.)

## Part 9 — Test end-to-end through Claude Code

```bash
cd /same/directory/as/step/8
claude
```

Inside the session:

```
/mcp
```

Confirm `navila-orcalab` shows as connected. Then:

```
Call the navila_health_check tool and tell me exactly what it returns.
```

Approve the permission prompt the first time it asks. Then:

```
Use navila_run_instruction to tell NaVILA to walk toward the red waste bin
and stop beside it. Tell me how many decisions it took, how it ended, and
what the final position was.
```

Watch the OrcaLab GUI — same robot movement as always, just triggered by
Claude instead of a manual terminal command.

---

## Quick troubleshooting reference

| Symptom | Likely cause |
|---|---|
| `navila_health_check` returns a connection error | AWS tunnel (Terminal 1) isn't open |
| Script exits with code 2 immediately | orca-lab/orca-gym version mismatch — check `raw_stderr_tail` in the result |
| Runs but times out, no `measurements.json` | OrcaLab GUI simulation wasn't actually started in Terminal 2 |
| `python navila_bridge.py` just sits there with no output | Expected — that starts the actual MCP server waiting for a client. Use `Ctrl+C` and run functions directly instead for testing (Part 5) |
| `claude mcp list` shows failed/disconnected | Wrong interpreter path in Part 8, or `mcp` package not installed in that specific env |
| `claude mcp add` succeeded but session doesn't see the server | You started `claude` from a different directory than where you ran `claude mcp add` |
| `mcp dev` fails with a `node:util`/`styleText` error | Node.js is older than v20 — this step is optional, skip it or upgrade Node |

## Known open issues to be aware of

1. **Unconfirmed JSON serialization bug** (see Part 5) — not yet root-caused.
2. **No live progress during an episode** — the bridge uses a blocking
   subprocess call, so neither you nor Claude sees anything until the whole
   multi-minute episode finishes. Fine for now; a future improvement would
   stream output live instead.
3. **This wraps a full episode as one tool call** (Phase 1 of our plan) — it
   does not give Claude visibility into individual camera frames or the
   ability to intervene mid-episode. See the earlier findings/plan doc for
   what a more fine-grained Phase 2 would involve, and why we deliberately
   didn't start there.

## Changes made to the script since the version tested above

- Added `elapsed_s` to the timeout-error return branch (it was missing,
  which is part of why the failure diagnosis took longer than it should
  have).
- Raised the default timeout from 300s to 900s, since a real ~27-decision
  episode did not reliably finish within 300s during testing.
