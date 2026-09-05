"""Does the a_workspace safety wrapping survive contact with the REAL
MjlabGo2Backend? Run this before believing any of it.

The unit tests in tests/ fake the physics backend. This script does not: it
builds a real MjlabGo2Backend (MJLab/MJWarp, real go2_flat.pt policy), wraps it
with build_safe_runner(), and checks the robot actually freezes when the
watchdog trips. It still fakes the renderer and the driver VLM, because neither
is what's under test here and both would drag in OrcaLab and the AWS tunnel.

No OrcaLab GUI, no AWS SSM tunnel and no GPU required -- MjlabGo2Backend
defaults to device="cpu". CPU is slow, so this takes a few minutes.

Run from the repo root, with the orcalab env's interpreter:

    PYTHONIOENCODING=utf-8 \
    PYTHONPATH=NaVILA-Orca/src \
    .conda/envs/orcalab/python.exe a_workspace/check_real_backend.py

Add --device cuda if you want it on the GPU box (much faster).
"""

from __future__ import annotations

import argparse
import traceback
from pathlib import Path

import numpy as np

from navila_orca.contracts import EpisodeSpec, RenderFrame, RobotState
from navila_orca.robot_backend.mock_force_sensor import MockForceSensor
from navila_orca.safety_watchdog import SafeForceBand

from navila_orca.safety_integration import build_safe_runner


class NullRenderBridge:
    """Black frames with the right step_id. The renderer is not what's under
    test here -- swapping this for the real OrcaLab bridge is step 2."""

    def render(self, state: RobotState, qpos_batch=None) -> RenderFrame:
        return RenderFrame(
            step_id=state.step_id,
            sim_time_s=state.sim_time_s,
            camera_id="null",
            rgb=np.zeros((64, 64, 3), dtype=np.uint8),
        )

    def close(self) -> None:
        pass


class ScriptedVLM:
    """Always asks to walk forward, so the run keeps moving until something
    else stops it. That 'something else' is the whole point of the test."""

    def __init__(self, action: str = "move forward 75cm") -> None:
        self.action = action
        self.calls = 0

    def infer(self, images, instruction) -> str:
        self.calls += 1
        return self.action


class StubVetoClient:
    def __init__(self, verdict: str = "CLEAR") -> None:
        self.verdict = verdict
        self.calls = 0

    def query(self, image, instruction, proposed_action) -> str:
        self.calls += 1
        return self.verdict


def episode() -> EpisodeSpec:
    return EpisodeSpec(
        episode_id="real-backend-check",
        scene_id="flat",
        instruction="walk forward",
        start_position=np.zeros(3),
        start_quat_wxyz=np.array([1.0, 0.0, 0.0, 0.0]),
        goal_position=np.array([1000.0, 0.0, 0.0]),  # unreachable on purpose
        goal_radius=0.1,
        reference_path=np.zeros((2, 3)),
        gt_locations=np.zeros((2, 3)),
    )


def make_backend(device: str, warmup: int):
    from navila_orca.backends.mjlab_go2 import MjlabGo2Backend

    return MjlabGo2Backend(device=device, num_envs=1, warmup_steps=warmup)


def scenario(name: str, *, device: str, warmup: int, drop_at: int | None, verdict: str,
             max_decisions: int):
    backend = make_backend(device, warmup)
    sensor = MockForceSensor()
    if drop_at is not None:
        sensor.schedule_drop(at_step=drop_at, duration_steps=10_000)

    vlm = ScriptedVLM()
    veto_client = StubVetoClient(verdict)

    # Derive the frame/stream intervals from the backend's own control_dt so
    # runner.py's exact-tick-divisibility check can never fail here.
    probe = backend
    control_dt = float(probe.control_dt)

    runner, state, watchdog, veto_agent, logbook = build_safe_runner(
        backend,
        NullRenderBridge(),
        vlm,
        veto_client,
        band=SafeForceBand(low=20.0, high=80.0),
        debounce_ticks=3,
        force_reader=sensor.read,
        scene_fidelity=False,
        max_decisions=max_decisions,
        image_interval_s=control_dt * 10,
        state_stream_interval_s=control_dt * 5,
    )

    result = runner.run(episode())
    x = float(result.final_state.root_pos_world[0])
    y = float(result.final_state.root_pos_world[1])

    # Measure travel with NavigationMetrics' own path_length, NOT distance from
    # the world origin. MjlabGo2Backend.reset() runs a zero-velocity warmup of
    # real physics steps to settle the dog on its feet ("outside the public
    # navigation clock"), and its reset events randomize the base pose, so the
    # robot is already a centimetre or so off origin before decision 1. Only
    # path_length measures distance actually travelled during the episode,
    # starting from the post-reset pose.
    dist = float(result.metrics.get("path_length", float("nan")))

    print(f"\n--- {name} ---")
    print(f"  termination_reason : {result.termination_reason}")
    print(f"  decisions          : {result.decisions}")
    print(f"  control_steps      : {result.control_steps}")
    print(f"  watchdog tripped   : {watchdog.tripped}")
    print(f"  veto calls made    : {veto_client.calls}")
    print(f"  final position     : x={x:+.3f} y={y:+.3f} (vs world origin; "
          "includes reset/warmup settling)")
    print(f"  path travelled     : {dist:.4f} m  <-- this is what the checks use")
    if logbook.entries():
        print("  logbook:")
        for entry in logbook.entries():
            print(f"    {entry.format()}")
    backend.close()
    return result, watchdog, dist


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cpu", help="cpu (default) or cuda")
    parser.add_argument("--warmup", type=int, default=20,
                        help="MjlabGo2Backend warmup_steps (default 20; the "
                             "backend's own default is 100, lowered here for speed)")
    parser.add_argument("--max-decisions", type=int, default=4)
    args = parser.parse_args()

    # Provenance check. Your orcalab env has an editable install pointing at
    # C:\\Users\\aadha\\Orca_VLN\\NaVILA-Orca\\src, so without an explicit
    # PYTHONPATH this script would happily test the OLD source tree and tell you
    # everything is fine. Print what actually got imported so there is no doubt.
    import navila_orca
    import navila_orca.safety_integration as _si
    print("Loaded navila_orca from   :", Path(navila_orca.__file__).parent)
    print("Loaded safety_integration :", Path(_si.__file__))
    expected = Path(__file__).resolve().parent.parent / "NaVILA-Orca" / "src" / "navila_orca"
    if Path(navila_orca.__file__).parent.resolve() != expected.resolve():
        print()
        print("!! WARNING: navila_orca did NOT come from this repo.")
        print(f"!! expected: {expected}")
        print("!! Re-run with PYTHONPATH pointed at this repo's NaVILA-Orca/src,")
        print("!! or you are testing a different checkout than the one you edited.")
        print()

    print(f"Building a REAL MjlabGo2Backend on device={args.device!r}. "
          "This is slow on CPU, be patient.")

    checks: list[tuple[str, bool, str]] = []

    baseline, wd_a, dist_a = scenario(
        "A: clean run, no force drop, veto always CLEAR",
        device=args.device, warmup=args.warmup, drop_at=None,
        verdict="CLEAR", max_decisions=args.max_decisions,
    )
    checks.append((
        "A1 robot actually walked with the wrapper in place",
        dist_a > 0.05,
        f"path travelled {dist_a:.3f} m, expected > 0.05",
    ))
    checks.append((
        "A2 watchdog did NOT trip when force was nominal",
        not wd_a.tripped,
        f"tripped={wd_a.tripped}, expected False",
    ))

    tripped, wd_b, dist_b = scenario(
        "B: harness force drops to zero mid-run",
        device=args.device, warmup=args.warmup, drop_at=5,
        verdict="CLEAR", max_decisions=args.max_decisions,
    )
    checks.append((
        "B1 watchdog tripped on the force drop",
        wd_b.tripped,
        f"tripped={wd_b.tripped}, expected True",
    ))
    checks.append((
        "B2 run ended as terminated (frozen), not by running out of decisions",
        tripped.termination_reason == "terminated",
        f"termination_reason={tripped.termination_reason!r}, expected 'terminated'",
    ))
    checks.append((
        "B3 robot stopped short of the clean run's distance",
        dist_b < dist_a,
        f"trip run travelled {dist_b:.3f} m vs clean run {dist_a:.3f} m",
    ))

    vetoed, wd_c, dist_c = scenario(
        "C: hazard veto on the very first decision",
        device=args.device, warmup=args.warmup, drop_at=None,
        verdict="VETO: pedestrian in the path", max_decisions=args.max_decisions,
    )
    checks.append((
        "C1 veto ended the run before any physics ran",
        vetoed.control_steps == 0,
        f"control_steps={vetoed.control_steps}, expected 0",
    ))
    checks.append((
        "C2 robot travelled nowhere during the episode",
        dist_c < 1e-6,
        f"path travelled {dist_c:.6f} m, expected 0 (reset/warmup settling is "
        f"excluded by using path_length, not distance from origin)",
    ))

    print("\n" + "=" * 68)
    failed = 0
    for label, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}\n         {detail}")
        failed += 0 if ok else 1
    print("=" * 68)
    if failed:
        print(f"\n{failed} of {len(checks)} checks FAILED. The wrapper does NOT "
              "behave correctly against the real backend. Do not trust the unit "
              "tests alone.")
        return 1
    print(f"\nAll {len(checks)} checks passed against the real MjlabGo2Backend.")
    print("Next: swap NullRenderBridge for the real OrcaLab render bridge and "
          "confirm the freeze is visible in the GUI.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        print("\nThis crashed rather than failing a check. That is itself a "
              "finding: something about the real backend differs from what the "
              "unit tests' fake assumes.")
        raise SystemExit(2)
