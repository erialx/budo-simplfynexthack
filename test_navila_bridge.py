"""Test cases for the Stage 1 per-step bridge (navila_bridge.py + bridge_backends.py).

Run with pytest if available:
    pytest test_navila_bridge.py -v

Or with no dependencies beyond the orcalab-phys env (no pytest needed):
    /home/guest/miniconda3/envs/orcalab-phys/bin/python test_navila_bridge.py

Every test drives the MockBackend + MockVLM (NAVILA_BRIDGE_BACKEND=mock is the
default), so none of this needs the AWS tunnel, the OrcaLab GUI, or a GPU.
"""

from __future__ import annotations

import json
import math
import os

import navila_bridge as bridge

TOOLS = [
    bridge.navila_health_check,
    bridge.navila_run_instruction,
    bridge.navila_start_episode,
    bridge.navila_navigate_step,
    bridge.navila_get_status,
    bridge.navila_emergency_stop,
    bridge.navila_reset_episode,
]


def _fresh():
    """Every test gets a clean session -- these tools share one module-level
    singleton by design (mirrors the one long-lived bridge process)."""
    bridge._SESSION.close()
    return bridge._SESSION


# ---------------------------------------------------------------------------
# 1. json.dumps safety (the task this session root-caused and fixed)
# ---------------------------------------------------------------------------

def test_jsonable_coerces_numpy_scalars_and_arrays():
    import numpy as np

    payload = {
        "arr": np.array([1.0, 2.0, 3.0], dtype=np.float32),
        "scalar32": np.float32(0.5),
        "scalar64": np.int64(7),
        "nested": {"x": np.float32(1.0), "y": [np.int64(1), np.int64(2)]},
    }
    safe = bridge._jsonable(payload)
    json.dumps(safe)  # must not raise
    assert safe["arr"] == [1.0, 2.0, 3.0]
    assert safe["scalar32"] == 0.5
    assert safe["scalar64"] == 7
    assert safe["nested"]["y"] == [1, 2]


def test_every_mcp_tool_return_survives_json_dumps():
    _fresh()
    start = bridge.navila_start_episode("go to the marker", goal_x=1.0, goal_y=1.0)
    # no-arg tools only; health_check/run_instruction need live services and
    # start_episode needs its instruction arg (both exercised in other tests).
    no_arg_tools = [
        bridge.navila_navigate_step,
        bridge.navila_get_status,
        bridge.navila_emergency_stop,
        bridge.navila_reset_episode,
    ]
    for result in [start, *(tool() for tool in no_arg_tools)]:
        json.dumps(result)  # each tool's return must be JSON-safe on its own
        assert isinstance(result, dict) and "ok" in result


def test_run_instruction_bad_input_is_jsonable():
    result = bridge.navila_run_instruction("")
    assert result == {"ok": False, "error": "instruction must be a non-empty string"}
    json.dumps(result)


# ---------------------------------------------------------------------------
# 2. Episode lifecycle: idle -> running -> done
# ---------------------------------------------------------------------------

def test_navigate_step_before_start_returns_error_not_exception():
    s = _fresh()
    result = s.navigate_step()
    assert result == {
        "ok": False,
        "error": "no active episode; call navila_start_episode first",
    }


def test_start_episode_rejects_empty_instruction():
    s = _fresh()
    result = s.start_episode("   ")
    assert result["ok"] is False
    assert "non-empty" in result["error"]
    assert s.phase == "idle"


def test_scripted_episode_runs_actions_in_order_and_stops():
    s = _fresh()
    r = s.start_episode(
        "walk to the door",
        vlm_script="move forward by 75 cm; turn left by 30 degrees; move forward by 50 cm; stop",
        max_decisions=10,
    )
    assert r["ok"] is True and r["phase"] == "running"

    actions = []
    for _ in range(10):
        step = s.navigate_step()
        actions.append(step["action"])
        if step["done"]:
            assert step["termination_reason"] == "stop"
            break
    else:
        raise AssertionError("episode did not finish within 10 steps")

    assert actions == [
        "move forward by 75 cm",
        "turn left by 30 degrees",
        "move forward by 50 cm",
        "stop",
    ]
    # 75cm forward, then a 30deg turn, then 50cm forward from the new heading.
    pose = s.status()["pose"]
    assert math.isclose(pose["yaw_deg"], 30.0, abs_tol=1e-6)
    assert pose["x"] > 0.75  # moved further forward after turning
    assert pose["y"] > 0.0   # turned left before the second move -> some +y drift


def test_status_does_not_advance_physics():
    s = _fresh()
    s.start_episode("go", vlm_script="move forward by 75 cm; stop")
    s.navigate_step()
    before = s.status()
    after = s.status()
    assert before["control_steps"] == after["control_steps"] == 75
    assert before["decision_index"] == after["decision_index"] == 1


def test_reset_episode_clears_counters_and_replays_same_script():
    s = _fresh()
    s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=5)
    s.navigate_step()
    assert s.control_steps == 75
    r = s.reset_episode()
    assert r["ok"] is True
    assert r["phase"] == "running"
    assert r["control_steps"] == 0
    assert r["decision_index"] == 0
    # Fresh script pointer -> first action is 'move forward by 75 cm' again.
    step = s.navigate_step()
    assert step["action"] == "move forward by 75 cm"


def test_reset_without_prior_episode_errors_cleanly():
    s = _fresh()
    result = s.reset_episode()
    assert result["ok"] is False
    assert "no previous episode" in result["error"]


# ---------------------------------------------------------------------------
# 3. Safe-STOP on malformed VLM output (CLAUDE.md rule: never crash, never
#    silently retry)
# ---------------------------------------------------------------------------

def test_unparseable_vlm_output_forces_safe_stop_not_a_crash():
    s = _fresh()
    s.start_episode("x", vlm_script="go somewhere vaguely")
    step = s.navigate_step()  # must not raise ActionParseError up through this call
    assert step["ok"] is True
    assert step["action"] == "stop"
    assert step["phase"] == "done"
    assert step["termination_reason"] == "parse_error"


def test_ambiguous_vlm_output_also_forces_safe_stop():
    s = _fresh()
    s.start_episode("x", vlm_script="move forward by 50 cm and turn left by 30 degrees")
    step = s.navigate_step()
    assert step["action"] == "stop"
    assert step["termination_reason"] == "parse_error"


def test_out_of_grammar_amount_forces_safe_stop():
    # 40 is not one of the canonical {25, 50, 75} cm amounts.
    s = _fresh()
    s.start_episode("x", vlm_script="move forward by 40 cm")
    step = s.navigate_step()
    assert step["action"] == "stop"
    assert step["termination_reason"] == "parse_error"


# ---------------------------------------------------------------------------
# 3b. VLM call itself failing (socket timeout / connection refused / protocol
#     error) -- distinct from the VLM answering with bad *text*. Must degrade
#     to a safe STOP, never hang or propagate out of the tool call.
# ---------------------------------------------------------------------------

class _FailingVLM:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def next_action(self, **kwargs):
        raise self._exc


def test_vlm_socket_timeout_forces_safe_stop_not_a_hang():
    import socket

    s = _fresh()
    s.start_episode("go", goal_x=5.0, goal_y=0.0)
    s.vlm = _FailingVLM(socket.timeout("timed out"))
    step = s.navigate_step()
    assert step["ok"] is True
    assert step["action"] == "stop"
    assert step["phase"] == "done"
    assert step["termination_reason"] == "vlm_error"
    assert "timed out" in step["note"]


def test_vlm_connection_refused_forces_safe_stop():
    s = _fresh()
    s.start_episode("go", goal_x=5.0, goal_y=0.0)
    s.vlm = _FailingVLM(ConnectionRefusedError("no server listening"))
    step = s.navigate_step()
    assert step["action"] == "stop"
    assert step["termination_reason"] == "vlm_error"


def test_vlm_timeout_s_only_reaches_tcp_backend_not_mock():
    # mock_vlm never accepts a timeout_s kwarg -- must not blow up start_episode.
    s = _fresh()
    result = s.start_episode(
        "go", vlm_script="stop", vlm_kind="mock", vlm_timeout_s=5.0
    )
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# 4. Goal-seeking + goal_reached termination
# ---------------------------------------------------------------------------

def test_goal_seeking_mock_vlm_converges_and_stops_in_radius():
    s = _fresh()
    s.start_episode("go to the marker", goal_x=2.0, goal_y=1.0, goal_radius=0.4, max_decisions=30)
    last = None
    for _ in range(30):
        last = s.navigate_step()
        if last["done"]:
            break
    assert last is not None and last["done"] is True
    assert last["termination_reason"] == "goal_reached"
    assert last["distance_to_goal"] <= 0.4


def test_max_decisions_caps_an_episode_that_never_reaches_goal():
    s = _fresh()
    # Goal picked far enough away that 3 decisions cannot reach goal_radius.
    s.start_episode("go far", goal_x=50.0, goal_y=50.0, goal_radius=0.1, max_decisions=3)
    last = None
    for _ in range(10):  # loop bound higher than max_decisions on purpose
        last = s.navigate_step()
        if last["done"]:
            break
    assert last["termination_reason"] == "max_decisions"
    assert last["decision_index"] == 3


def test_max_control_steps_caps_mid_chunk():
    s = _fresh()
    s.start_episode(
        "go far", vlm_script="move forward by 75 cm", max_control_steps=10
    )
    step = s.navigate_step()
    assert step["termination_reason"] == "max_control_steps"
    assert step["executed_ticks"] == 10
    assert step["control_steps"] == 10


# ---------------------------------------------------------------------------
# 5. emergency_stop preempts the loop (Stage 3 Safety Watchdog seam)
# ---------------------------------------------------------------------------

def test_emergency_stop_halts_a_running_episode():
    s = _fresh()
    s.start_episode("go far", goal_x=10.0, goal_y=0.0, max_decisions=30)
    s.navigate_step()
    assert s.phase == "running"

    stop_result = s.emergency_stop()
    assert stop_result["ok"] is True
    assert stop_result["phase"] == "stopped"
    assert stop_result["termination_reason"] == "emergency_stop"
    assert stop_result["stop_path"] == "backend.emergency_stop()"

    # further navigate_step calls must not resume motion
    control_steps_at_stop = s.control_steps
    again = s.navigate_step()
    assert again["action"] == "stop"
    assert again["phase"] == "stopped"
    assert s.control_steps == control_steps_at_stop  # no further physics steps


def test_emergency_stop_mid_chunk_via_interrupt_flag_pre_check():
    """Models the real seam: a Safety Watchdog in the same process flips
    backend.interrupted directly (bypassing the Orchestrator's MCP calls)."""
    s = _fresh()
    s.start_episode("go far", vlm_script="move forward by 75 cm")
    s.backend.interrupted = True  # simulate the Watchdog firing out of band
    step = s.navigate_step()
    assert step["action"] == "stop"
    assert step["phase"] == "stopped"
    assert step["termination_reason"] == "emergency_stop"
    assert step["control_steps"] == 0  # no physics executed after the interrupt


def test_emergency_stop_with_no_active_backend_is_a_noop_not_an_error():
    s = _fresh()
    result = s.emergency_stop()
    assert result == {"ok": True, "phase": "idle", "note": "no active backend"}


# ---------------------------------------------------------------------------
# 6. Physics-side termination (a "fall")
# ---------------------------------------------------------------------------

def test_backend_termination_mid_chunk_ends_episode_as_done_terminated():
    s = _fresh()
    s.start_episode("walk", vlm_script="move forward by 75 cm", max_decisions=5)
    s.backend._terminate_after_steps = 5  # force MockBackend to "fall" on tick 5
    step = s.navigate_step()
    assert step["phase"] == "done"
    assert step["termination_reason"] == "terminated"
    assert step["executed_ticks"] == 5  # stopped short of the requested 75 ticks
    assert step["requested_ticks"] == 75


# ---------------------------------------------------------------------------
# 7. MockVLM script exhaustion defaults to stop (never hangs, never repeats)
# ---------------------------------------------------------------------------

def test_scripted_vlm_defaults_to_stop_once_script_is_exhausted():
    s = _fresh()
    s.start_episode("go", vlm_script="move forward by 75 cm")  # single-action script
    first = s.navigate_step()
    assert first["done"] is False
    second = s.navigate_step()  # script exhausted -> MockVLM.next_action() -> "stop"
    assert second["action"] == "stop"
    assert second["termination_reason"] == "stop"


# ---------------------------------------------------------------------------
# 8. Hazard Veto Agent gate (Stage 3 differentiator)
# ---------------------------------------------------------------------------

def test_hazard_veto_blocks_motion_and_ends_episode():
    s = _fresh()
    s.clear_hazards()  # _hazard_events persists across _fresh() by design (like
    # _force_events); start every test from a known-clean schedule.
    r = s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=5)
    assert r["ok"] is True and r["veto_enabled"] is True
    s.inject_hazard(at_step=1)

    step = s.navigate_step()
    assert step["done"] is True
    assert step["termination_reason"] == "veto"
    assert step["action"] == "stop"
    assert "veto_reason" in step
    assert step["control_steps"] == 0  # gated before any physics ran


def test_hazard_veto_records_in_decision_logbook():
    s = _fresh()
    s.clear_hazards()
    s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=5)
    s.inject_hazard(at_step=1)
    s.navigate_step()

    entries = s.get_logbook()["entries"]
    assert any(e["kind"] == "VETO" and e["source"] == "hazard_veto" for e in entries)


def test_no_hazard_injected_veto_enabled_runs_normally():
    s = _fresh()
    s.clear_hazards()
    s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=5)

    step = None
    for _ in range(5):
        step = s.navigate_step()
        if step["done"]:
            break
    assert step["termination_reason"] == "stop"  # not a false-positive veto


def test_veto_disabled_via_kwarg_ignores_injected_hazard():
    s = _fresh()
    s.clear_hazards()
    r = s.start_episode(
        "go", vlm_script="move forward by 75 cm; stop", max_decisions=5, veto=False
    )
    assert r["veto_enabled"] is False
    s.inject_hazard(at_step=1)

    step = s.navigate_step()
    assert step["action"] == "move forward by 75 cm"
    assert step["moved_m"] > 0
    assert step["termination_reason"] != "veto"


def test_veto_disabled_via_env_var():
    s = _fresh()
    s.clear_hazards()
    os.environ["NAVILA_BRIDGE_VETO"] = "0"
    try:
        s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=5)
        s.inject_hazard(at_step=1)
        step = s.navigate_step()
    finally:
        del os.environ["NAVILA_BRIDGE_VETO"]

    assert step["action"] == "move forward by 75 cm"
    assert step["termination_reason"] != "veto"


def test_inject_hazard_persists_across_reset_episode():
    s = _fresh()
    s.clear_hazards()
    s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=5)
    s.inject_hazard(at_step=1)

    first = s.navigate_step()
    assert first["termination_reason"] == "veto"

    s.reset_episode()
    second = s.navigate_step()
    assert second["termination_reason"] == "veto"


def test_clear_hazards_removes_scheduled_injection():
    s = _fresh()
    s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=5)
    s.inject_hazard(at_step=1)
    s.clear_hazards()

    step = s.navigate_step()
    assert step["termination_reason"] != "veto"
    assert step["action"] == "move forward by 75 cm"


# ---------------------------------------------------------------------------
# 9. WAYPOINT_STOP_OVERRIDE precedence flag (docs/PLAN.md, "C" item 2)
#
# stop_override_suppressed has no consumer in this file yet -- it's a
# ready-made check for a future port of D's runner.py forward-nudge reflex
# into this per-step loop. These tests only pin down when the flag itself
# gets set, not any override behavior (there is none here to suppress).
# ---------------------------------------------------------------------------

def test_stop_override_suppressed_after_veto():
    s = _fresh()
    s.clear_hazards()
    s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=5)
    s.inject_hazard(at_step=1)

    step = s.navigate_step()
    assert step["termination_reason"] == "veto"
    assert step["stop_override_suppressed"] is True


def test_stop_override_suppressed_after_watchdog_interrupt():
    s = _fresh()
    s.start_episode("go far", vlm_script="move forward by 75 cm")
    s.backend.interrupted = True  # simulate the Watchdog firing out of band

    step = s.navigate_step()
    assert step["termination_reason"] == "emergency_stop"
    assert step["stop_override_suppressed"] is True


def test_stop_override_not_suppressed_by_ordinary_vlm_stop():
    """A plain VLM-issued stop ('reached the goal') is not a safety event --
    it must NOT suppress a future forward-nudge reflex the way a watchdog trip
    or a veto does."""
    s = _fresh()
    s.clear_hazards()
    s.start_episode("go", vlm_script="stop", max_decisions=5)

    step = s.navigate_step()
    assert step["termination_reason"] == "stop"
    assert step["stop_override_suppressed"] is False


def test_stop_override_not_suppressed_on_normal_motion_step():
    s = _fresh()
    s.clear_hazards()
    s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=5)

    step = s.navigate_step()
    assert step["action"] == "move forward by 75 cm"
    assert step["stop_override_suppressed"] is False


if __name__ == "__main__":
    # Zero-dependency runner: pytest isn't installed in either conda env here.
    import sys
    import traceback

    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
        except Exception:  # noqa: BLE001
            failed.append(name)
            print(f"FAIL {name}")
            traceback.print_exc()
        else:
            passed += 1
            print(f"PASS {name}")
    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        print("Failed:", ", ".join(failed))
        sys.exit(1)
