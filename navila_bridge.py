#!/usr/bin/env python3
"""Phase 1 MCP bridge: Claude Code <-> NaVILA-Orca / OrcaLab locomotion pipeline.

This does NOT reimplement the VLM socket protocol, action parsing, or physics
stepping. It wraps the existing, already-working
`run_orcalab_scene_locomotion.sh` script as a single MCP tool and parses its
`measurements.json` output into a structured result.

REQUIRED BEFORE THIS WILL WORK:
  1. Terminal 1: the AWS SSM tunnel must be open (localhost:54321 reachable).
  2. Terminal 2: the OrcaLab GUI must be open, factory.json loaded, and the
     simulation started (manual launch mode) -- exactly as in your normal
     3-terminal setup.
  3. This script must be run with the `orcalab` conda environment's Python
     interpreter (it shells out to a script that itself needs that
     environment; the MCP process doesn't strictly need orcalab.* imports
     itself, but keeping it in the same env avoids any PATH/env-var surprises).

Install the MCP SDK first (inside the orcalab env):
    pip install "mcp[cli]"
"""

import json
import socket
import subprocess
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("navila-orcalab-bridge")

# ---------------------------------------------------------------------------
# Configuration -- confirmed from your own measurements.json / setup guide.
# Double-check these still match your machine before relying on this.
# ---------------------------------------------------------------------------
ORCA_VLN_ROOT = Path("/home/guest/simplifynext/budo-simplfynexthack/NaVILA-Orca")
LOCOMOTION_SCRIPT = ORCA_VLN_ROOT / "NaVILA-Orca" / "scripts" / "run_orcalab_scene_locomotion.sh"
MEASUREMENTS_PATH = ORCA_VLN_ROOT / "NaVILA-Orca" / "outputs" / "scene_locomotion_smoke" / "measurements.json"
VLM_HOST = "127.0.0.1"
VLM_PORT = 54321
DEFAULT_TIMEOUT_S = 900  # 15 minutes. 300s proved too short in real testing --
                          # a ~27-decision episode did not reliably finish in time.
TAIL_CHARS = 2000  # how much of stdout/stderr to keep on failure, for debugging

METRICS_CAVEAT = (
    "scene_fidelity is false and Matterport 3DGS collision geometry is not "
    "installed in this environment, so 'success'/'spl' here are "
    "protocol-compatibility placeholders, not real navigation-quality "
    "evidence. Use termination_reason and vlm_outputs as the source of truth "
    "for what actually happened, not the success/spl numbers."
)


# ---------------------------------------------------------------------------
# Plain, undecorated implementations -- these are the ones to unit-test
# directly without going through MCP or Claude Code at all. See the testing
# instructions for exactly how.
# ---------------------------------------------------------------------------

def _health_check_impl() -> dict:
    """Confirm the NaVILA VLM server is reachable through the SSM tunnel."""
    request = json.dumps({"type": "health"}, separators=(",", ":")).encode()
    try:
        with socket.create_connection((VLM_HOST, VLM_PORT), timeout=5.0) as sock:
            sock.sendall(len(request).to_bytes(8, "big"))
            sock.sendall(request)

            size_bytes = sock.recv(8)
            if len(size_bytes) != 8:
                return {"ok": False, "error": "connection closed before size header arrived"}
            size = int.from_bytes(size_bytes, "big")

            buf = b""
            while len(buf) < size:
                chunk = sock.recv(size - len(buf))
                if not chunk:
                    break
                buf += chunk
            response = json.loads(buf.decode("utf-8"))
    except (ConnectionRefusedError, socket.timeout, OSError) as exc:
        return {
            "ok": False,
            "error": (
                f"cannot reach NaVILA at {VLM_HOST}:{VLM_PORT}: {exc}. "
                "Is Terminal 1's SSM tunnel still open?"
            ),
        }

    return {"ok": response.get("status") == "ok", "response": response}


def _run_instruction_impl(instruction: str, timeout_s: int) -> dict:
    """Run one full NaVILA episode for `instruction` and parse the result."""
    if not instruction or not instruction.strip():
        return {"ok": False, "error": "instruction must be a non-empty string"}
    if not LOCOMOTION_SCRIPT.exists():
        return {"ok": False, "error": f"script not found at {LOCOMOTION_SCRIPT}"}

    started_at = time.time()
    try:
        result = subprocess.run(
            [str(LOCOMOTION_SCRIPT), "--instruction", instruction],
            cwd=str(ORCA_VLN_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "elapsed_s": round(time.time() - started_at, 1),
            "error": f"episode timed out after {timeout_s}s",
            "raw_stdout_tail": (exc.stdout or "")[-TAIL_CHARS:],
            "raw_stderr_tail": (exc.stderr or "")[-TAIL_CHARS:],
        }

    elapsed_s = round(time.time() - started_at, 1)

    if result.returncode != 0:
        return {
            "ok": False,
            "error": f"script exited with code {result.returncode}",
            "elapsed_s": elapsed_s,
            "raw_stdout_tail": result.stdout[-TAIL_CHARS:],
            "raw_stderr_tail": result.stderr[-TAIL_CHARS:],
        }

    if not MEASUREMENTS_PATH.exists():
        return {
            "ok": False,
            "error": f"script exited 0 but measurements.json not found at {MEASUREMENTS_PATH}",
            "elapsed_s": elapsed_s,
            "raw_stdout_tail": result.stdout[-TAIL_CHARS:],
        }

    try:
        data = json.loads(MEASUREMENTS_PATH.read_text())
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"measurements.json is not valid JSON: {exc}", "elapsed_s": elapsed_s}

    metrics = data.get("metrics", {})
    final_state = data.get("final_state", {})
    episode = data.get("episode", {})

    return {
        "ok": True,
        "elapsed_s": elapsed_s,
        "instruction_run": episode.get("instruction"),
        "pipeline_status": data.get("pipeline_status"),
        "termination_reason": data.get("termination_reason"),
        "decisions": data.get("decisions"),
        "control_steps": data.get("control_steps"),
        "vlm_outputs": data.get("vlm_outputs", []),
        "final_position": final_state.get("root_pos_world"),
        "metrics": metrics,
        "metrics_caveat": METRICS_CAVEAT,
        "limitations": data.get("limitations", []),
    }


# ---------------------------------------------------------------------------
# MCP tool registrations -- thin wrappers around the implementations above.
# ---------------------------------------------------------------------------

@mcp.tool()
def navila_health_check() -> dict:
    """Check that the NaVILA VLM server is reachable before running a full
    episode. Fast (a few seconds) -- use this before navila_run_instruction,
    which can take minutes."""
    return _health_check_impl()


@mcp.tool()
def navila_run_instruction(instruction: str, timeout_s: int = DEFAULT_TIMEOUT_S) -> dict:
    """Run one full NaVILA navigation episode in OrcaLab for a given
    natural-language instruction (e.g. "walk toward the red waste bin and
    stop beside it"), and return a structured summary of what happened.

    This shells out to the existing run_orcalab_scene_locomotion.sh pipeline,
    which runs the whole closed loop internally (camera capture -> NaVILA
    query -> action parsing -> physics stepping) repeatedly until the model
    decides to stop or the episode times out. Requires the AWS SSM tunnel
    (Terminal 1) and the OrcaLab GUI with simulation running (Terminal 2) to
    already be up.
    """
    return _run_instruction_impl(instruction, timeout_s)


if __name__ == "__main__":
    mcp.run()
