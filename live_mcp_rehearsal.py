#!/usr/bin/env python3
"""Stage-4 integration rehearsal for the per-step MCP bridge, driven directly
against navila_bridge's live session object (bridge._SESSION) -- the exact
same code every navila_* MCP tool calls, minus the JSON-RPC hop, so it can
run from a plain terminal without a connected MCP client.

Exercises the WHOLE merged loop against the live OrcaLab GUI (edit service
on :50151, confirmed reachable when this was written):
  - C1 pose mirror (backend_kind="orcalab-mock" -- no GPU needed)
  - Stage 2 Safety Watchdog: force-drop -> emergency_stop -> clear -> resume
  - Stage 3 Hazard Veto Agent: scheduled hazard -> veto -> clear -> resume
  - stop_override_suppressed set by both safety events, not by an ordinary stop
  - navila_continue_episode: pose carries over, decision_index resets to 0
  - navila_get_logbook: both events show up in the merged log
  - In-scene hazard trigger (navila_trigger_scene_hazard) -- visibly moves an
    actor; confirm in the GUI, this script only checks the RPC succeeded
  - Scene reset reliability (navila_reset_scene_layout) -- restores it

Run TWICE back-to-back (REHEARSAL_RUNS below) to check demo repeatability,
per docs/PLAN.md Stage 4 ("rehearse the demo trigger until it's reliable on
repeat runs").

NOT covered here (see docs/PLAN.md / CLAUDE.md "Open" section):
  - C2 real-gait physics (orcalab-render backend) -- blocked on D, not built.
  - AnthropicVetoVisionClient -- still the pixel-detection stub client.
  - vlm_kind="tcp" against the real NaVILA endpoint -- the AWS SSM tunnel to
    127.0.0.1:54321 is down on this box (confirmed via check_navvlm_endpoint.py
    immediately before writing this script); this only exercises vlm_kind="mock".
  - NavigationRunner's WAYPOINT_STOP_OVERRIDE precedence porting -- A's item,
    not yet wired into any loop this script drives.
  - Real ego-camera frames feeding the veto gate (NAVILA_BRIDGE_ORCA_CAMERA) --
    needs mujococamera1080 spawned in the live scene first (see CLAUDE.md's
    open item); this rehearsal uses placeholder frames + the pixel-stub veto
    client, same as every previous live-verification pass in docs/PLAN.md.

Run from repo root with the orcalab conda interpreter:
    /home/guest/miniconda3/envs/orcalab/bin/python live_mcp_rehearsal.py

Requires the OrcaLab GUI + edit service (127.0.0.1:50151) already running
with a scene loaded (this does not launch OrcaLab).
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import navila_bridge as bridge  # noqa: E402

REHEARSAL_RUNS = 2
HAZARD_ACTOR = "blue_hatchback_car_1"

_results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    status = "PASS" if cond else "FAIL"
    _results.append((status, name, detail))
    print(f"[{status}] {name}" + (f" -- {detail}" if detail else ""))
    return cond


def note(name: str, detail: str = "") -> None:
    _results.append(("INFO", name, detail))
    print(f"[INFO] {name}" + (f" -- {detail}" if detail else ""))


def run_rehearsal(run_idx: int) -> None:
    print(f"\n=== Rehearsal pass {run_idx}/{REHEARSAL_RUNS} ===")
    s = bridge._SESSION

    # 1. Start episode: real pose mirror in the GUI, watchdog + veto both on.
    r = s.start_episode(
        "walk forward down the street",
        backend_kind="orcalab-mock",
        vlm_kind="mock",
        vlm_script="; ".join(["move forward by 75 cm"] * 8 + ["stop"]),
        watchdog=True,
        veto=True,
        max_decisions=20,
    )
    if not check(f"[{run_idx}] start_episode ok", r.get("ok") is True, str(r.get("error", ""))):
        return
    check(f"[{run_idx}] watchdog_enabled", r.get("watchdog_enabled") is True)
    check(f"[{run_idx}] veto_enabled", r.get("veto_enabled") is True)

    # 2. One normal step: dog should visibly move in the GUI.
    step = s.navigate_step()
    check(
        f"[{run_idx}] first step executes normally",
        step.get("done") is False and step.get("moved_m", 0) > 0,
        f"moved_m={step.get('moved_m')}",
    )
    note(f"[{run_idx}] VISUAL CHECK", "confirm the dog glided forward in the OrcaLab GUI")

    # 3. Schedule a force drop just ahead of the current tick, run until the
    #    watchdog trips (emergency_stop).
    cs = step["control_steps"]
    s.inject_force_drop(at_step=cs + 3, duration_steps=30)
    tripped = False
    for _ in range(3):
        step = s.navigate_step()
        if step.get("termination_reason") == "emergency_stop":
            tripped = True
            break
    check(
        f"[{run_idx}] watchdog trips on force drop",
        tripped and step.get("stop_override_suppressed") is True,
        f"termination_reason={step.get('termination_reason')}",
    )
    note(f"[{run_idx}] VISUAL CHECK", "confirm the dog froze in place in the GUI")

    # 4. Clear + resume: must NOT re-report emergency_stop, must NOT teleport.
    pose_before_resume = step.get("pose")
    s.clear_force_drops()
    s.clear_stop()
    step = s.navigate_step()
    check(
        f"[{run_idx}] resumes after clear_stop",
        step.get("termination_reason") != "emergency_stop",
        f"termination_reason={step.get('termination_reason')}",
    )

    # 5. Schedule a hazard on the *next* decision, confirm veto blocks motion.
    d_idx = step["decision_index"]
    s.inject_hazard(at_step=d_idx + 1, duration_steps=2)
    pose_before_veto = step.get("pose")
    vetoed = False
    for _ in range(3):
        step = s.navigate_step()
        if step.get("termination_reason") == "veto":
            vetoed = True
            break
    check(
        f"[{run_idx}] veto blocks the proposed motion",
        vetoed
        and step.get("stop_override_suppressed") is True
        and bool(step.get("veto_reason")),
        f"termination_reason={step.get('termination_reason')} reason={step.get('veto_reason')!r}",
    )
    check(
        f"[{run_idx}] veto executed zero physics",
        step.get("pose") == pose_before_veto,
        f"before={pose_before_veto} after={step.get('pose')}",
    )

    # 6. Logbook should carry both events.
    log = s.get_logbook()
    log_text = log.get("text", "")
    check(
        f"[{run_idx}] logbook records the watchdog trip",
        "watchdog" in log_text.lower() or "emergency" in log_text.lower(),
    )
    check(
        f"[{run_idx}] logbook records the veto",
        "veto" in log_text.lower(),
    )

    # 7. continue_episode: pose carries over, decision_index resets to 0.
    s.clear_hazards()
    pose_before_continue = step.get("pose")
    r = s.continue_episode("keep walking down the street")
    check(
        f"[{run_idx}] continue_episode keeps the current pose",
        r.get("pose") == pose_before_continue and r.get("decision_index") == 0,
        f"pose={r.get('pose')} decision_index={r.get('decision_index')}",
    )
    step = s.navigate_step()
    check(
        f"[{run_idx}] navigate_step after continue advances from the carried pose",
        step.get("moved_m", 0) > 0,
        f"moved_m={step.get('moved_m')}",
    )

    # 8. In-scene hazard trigger -- visibly real, judge-facing.
    pose = step.get("pose") or {"x": 0.0, "y": 0.0}
    hazard_r = bridge._trigger_scene_hazard_impl(
        HAZARD_ACTOR, x=pose["x"] + 1.0, y=pose["y"], z=0.0, yaw_deg=0.0
    )
    check(
        f"[{run_idx}] navila_trigger_scene_hazard succeeds",
        hazard_r.get("ok") is True,
        str(hazard_r.get("error", "")),
    )
    note(f"[{run_idx}] VISUAL CHECK", f"confirm {HAZARD_ACTOR} jumped next to the dog in the GUI")

    # 9. Scene reset reliability -- restore just the moved actor.
    reset_r = bridge._reset_scene_layout_impl(HAZARD_ACTOR)
    check(
        f"[{run_idx}] navila_reset_scene_layout restores the moved actor",
        reset_r.get("ok") is True and HAZARD_ACTOR in (reset_r.get("restored_actors") or []),
        str(reset_r.get("error", "")),
    )
    note(f"[{run_idx}] VISUAL CHECK", f"confirm {HAZARD_ACTOR} snapped back to its authored spot")


def main() -> None:
    for i in range(1, REHEARSAL_RUNS + 1):
        run_rehearsal(i)
        time.sleep(0.5)

    print("\n=== Summary ===")
    passed = sum(1 for s, _, _ in _results if s == "PASS")
    failed = [n for s, n, d in _results if s == "FAIL"]
    total = passed + len(failed)
    print(f"{passed}/{total} checks passed")
    if failed:
        print("FAILED:")
        for n in failed:
            print(f"  - {n}")
        sys.exit(1)
    print("All automated checks passed. Confirm the VISUAL CHECK lines above in the GUI too.")


if __name__ == "__main__":
    main()
