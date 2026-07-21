#!/usr/bin/env python3
"""Stream a real Go2 MJWarp trajectory into OrcaLab without requiring a camera."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RSLRL_ROOT = PROJECT_ROOT / "components" / "OrcaLab-RSLRL"
for path in (PROJECT_ROOT / "src", RSLRL_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from navila_orca.backends.mjlab_go2 import MjlabGo2Backend  # noqa: E402
from navila_orca.render.orca import DEFAULT_GO2_ASSET  # noqa: E402
from orcalab_rslrl.orcalab_batch_render import OrcaLabBatchRenderer  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate MJWarp -> OrcaGym UpdateLocalEnv pose streaming."
    )
    parser.add_argument("--address", default="127.0.0.1:50051")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--vx", type=float, default=0.3)
    parser.add_argument("--asset-path", default=DEFAULT_GO2_ASSET)
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "outputs" / "orcalab_pose_smoke.json"),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.steps < 1:
        raise ValueError("--steps must be positive")

    backend = MjlabGo2Backend(device=args.device, num_envs=1)
    renderer = None
    try:
        backend.start()
        state = backend.reset(None)
        backend.set_velocity_command(args.vx, 0.0, 0.0)
        renderer = OrcaLabBatchRenderer(
            orcagym_addr=args.address,
            num_envs=1,
            joint_qpos_addr=backend.joint_qpos_addr,
            agent_prefix="go2",
            asset_path=args.asset_path,
            publish=not args.no_publish,
        )
        for _ in range(args.steps):
            state = backend.step().state
            renderer.render(backend.qpos_batch, state.sim_time_s)

        qpos = backend.qpos_batch
        payload = {
            "pipeline_status": "completed",
            "bridge": "OrcaGym UpdateLocalEnv",
            "steps": args.steps,
            "sim_time_s": state.sim_time_s,
            "finite": bool(np.all(np.isfinite(qpos))),
            "local_qpos_shape": list(qpos.shape),
            "combined_nq": int(renderer.layout.nq_combined),
            "mapped_values_per_env": int(renderer.layout.src_index.size),
            "agent_names": list(renderer.agent_names),
            "asset_path": args.asset_path,
            "combined_xml": str(renderer.combined_xml_path),
            "scene_fidelity": False,
        }
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print("ORCALAB_UPDATE_LOCAL_ENV_OK")
        print(json.dumps(payload, indent=2))
        print(f"result: {output}")
        return 0
    finally:
        try:
            if renderer is not None:
                renderer.close()
        finally:
            backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
