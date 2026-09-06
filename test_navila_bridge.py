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

import numpy as np

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
# 8b. Advisory veto mode (veto_mode="advisory"): a VETO blocks only that one
#     move, the episode keeps running so navila_navigate_step can be called
#     again -- for a full crossing test where the veto must protect every step
#     without aborting the mission.
# ---------------------------------------------------------------------------

def test_veto_mode_defaults_to_terminal():
    s = _fresh()
    s.clear_hazards()
    try:
        s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=5)
        s.inject_hazard(at_step=1)
        step = s.navigate_step()
    finally:
        s.clear_hazards()

    assert step["veto_mode"] == "terminal"
    assert step["done"] is True
    assert step["termination_reason"] == "veto"
    assert step["vetoed"] is True


def test_advisory_veto_blocks_move_but_episode_continues():
    s = _fresh()
    s.clear_hazards()
    try:
        s.start_episode(
            "go",
            vlm_script="move forward by 75 cm; move forward by 75 cm; stop",
            max_decisions=5,
            veto_mode="advisory",
        )
        s.inject_hazard(at_step=1, duration_steps=1)  # veto decision 1 only

        blocked = s.navigate_step()
        assert blocked["vetoed"] is True
        assert blocked["veto_mode"] == "advisory"
        assert blocked["done"] is False
        assert blocked["termination_reason"] is None
        assert blocked["control_steps"] == 0  # zero physics ran
        assert blocked["advisory_veto_count"] == 1
        assert blocked["stop_override_suppressed"] is True
        assert "veto_reason" in blocked

        moved = s.navigate_step()  # hazard gone -> NaVILA's move goes through
        assert moved.get("vetoed") is not True  # no 'vetoed' key on a clean step
        assert moved["termination_reason"] != "veto"
        assert moved["moved_m"] > 0
        assert moved["advisory_veto_count"] == 1  # unchanged, no new veto
    finally:
        s.clear_hazards()


def test_advisory_veto_still_records_each_block_in_the_logbook():
    s = _fresh()
    s.clear_hazards()
    try:
        s.start_episode(
            "go",
            vlm_script="move forward by 75 cm; move forward by 75 cm; stop",
            max_decisions=5,
            veto_mode="advisory",
        )
        s.inject_hazard(at_step=1, duration_steps=1)
        s.navigate_step()
        entries = s.get_logbook()["entries"]
    finally:
        s.clear_hazards()

    assert any(e["kind"] == "VETO" and e["source"] == "hazard_veto" for e in entries)


def test_advisory_veto_storm_ends_via_max_decisions_not_livelock():
    s = _fresh()
    s.clear_hazards()
    try:
        s.start_episode(
            "go",
            # more forward phrases than max_decisions so the script never
            # exhausts into a "stop" before the decision cap is hit
            vlm_script="; ".join(["move forward by 75 cm"] * 6),
            max_decisions=3,
            veto_mode="advisory",
        )
        s.inject_hazard(at_step=1, duration_steps=10)  # veto every decision

        last = None
        for _ in range(6):
            last = s.navigate_step()
            if last["done"]:
                break
    finally:
        s.clear_hazards()

    assert last["done"] is True
    assert last["termination_reason"] == "max_decisions"
    assert last["advisory_veto_count"] == 3
    assert last["control_steps"] == 0  # not one physics tick the whole time


def test_advisory_veto_mode_via_env_var():
    s = _fresh()
    s.clear_hazards()
    os.environ["NAVILA_BRIDGE_VETO_MODE"] = "advisory"
    try:
        s.start_episode(
            "go",
            vlm_script="move forward by 75 cm; stop",
            max_decisions=5,
        )
        s.inject_hazard(at_step=1, duration_steps=1)
        step = s.navigate_step()
    finally:
        del os.environ["NAVILA_BRIDGE_VETO_MODE"]
        s.clear_hazards()

    assert step["veto_mode"] == "advisory"
    assert step["vetoed"] is True
    assert step["done"] is False


def test_advisory_veto_mode_carries_across_continue_episode():
    s = _fresh()
    s.clear_hazards()
    try:
        s.start_episode(
            "leg one",
            vlm_script="move forward by 75 cm; stop",
            max_decisions=5,
            veto_mode="advisory",
        )
        s.inject_hazard(at_step=1, duration_steps=1)
        first = s.navigate_step()
        assert first["vetoed"] is True and first["done"] is False
        s.clear_hazards()

        cont = s.continue_episode("leg two")
        assert cont["ok"] is True
        assert s.status()["veto_mode"] == "advisory"

        # a fresh hazard on the new instruction is still handled advisory-style
        s.inject_hazard(at_step=1, duration_steps=1)
        step = s.navigate_step()
        assert step["veto_mode"] == "advisory"
        assert step["vetoed"] is True and step["done"] is False
    finally:
        s.clear_hazards()


def test_unknown_veto_mode_falls_back_to_terminal():
    s = _fresh()
    s.clear_hazards()
    try:
        s.start_episode(
            "go",
            vlm_script="move forward by 75 cm; stop",
            max_decisions=5,
            veto_mode="nonsense",
        )
        s.inject_hazard(at_step=1)
        step = s.navigate_step()
    finally:
        s.clear_hazards()

    assert step["veto_mode"] == "terminal"
    assert step["done"] is True and step["termination_reason"] == "veto"


# ---------------------------------------------------------------------------
# 8c. navila_get_ego_frame -- the Orchestrator's own look at the ego view, so
#     it can reroute NaVILA off the actual open space instead of guessing.
# ---------------------------------------------------------------------------

def test_get_ego_frame_errors_cleanly_when_idle():
    s = _fresh()
    meta, jpeg = s.current_ego_frame()
    assert meta["ok"] is False
    assert jpeg is None
    assert "no active episode" in meta["error"]


def test_get_ego_frame_returns_jpeg_and_metadata_when_active():
    s = _fresh()
    s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=3)

    meta, jpeg = s.current_ego_frame()
    assert meta["ok"] is True
    assert jpeg[:2] == b"\xff\xd8"  # JPEG SOI marker
    assert meta["real_capture"] is False  # mock backend -> 8x8 placeholder
    assert "placeholder" in meta["frame_desc"]
    assert meta["source_size"] == [8, 8]
    assert meta["decision_index"] == 0
    assert "hint" in meta  # placeholder -> tells you to fix the camera


def test_get_ego_frame_downscale_respects_max_edge_px():
    s = _fresh()
    s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=3)

    meta, jpeg = s.current_ego_frame(max_edge_px=4)
    assert max(meta["encoded_size"]) <= 4
    assert max(meta["encoded_size"]) <= max(meta["source_size"])
    assert jpeg[:2] == b"\xff\xd8"


def test_get_ego_frame_after_advisory_veto_is_the_blocked_frame():
    s = _fresh()
    s.clear_hazards()
    try:
        s.start_episode(
            "go",
            vlm_script="move forward by 75 cm; stop",
            max_decisions=5,
            veto_mode="advisory",
        )
        s.inject_hazard(at_step=1, duration_steps=1)
        blocked = s.navigate_step()
        assert blocked["vetoed"] is True and blocked["done"] is False

        meta, jpeg = s.current_ego_frame()
    finally:
        s.clear_hazards()

    assert meta["ok"] is True
    assert jpeg[:2] == b"\xff\xd8"
    assert meta["decision_index"] == 1  # the vetoed decision
    assert meta["advisory_veto_count"] == 1
    assert meta["last_veto_reason"]  # populated, so the Orchestrator can act on it


def test_navila_get_ego_frame_tool_returns_text_then_image():
    s = _fresh()
    s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=3)

    out = bridge.navila_get_ego_frame()
    assert isinstance(out, list) and len(out) == 2
    assert type(out[0]).__name__ == "TextContent"
    assert json.loads(out[0].text)["ok"] is True
    assert type(out[1]).__name__ == "Image"


def test_navila_get_ego_frame_tool_text_only_when_idle():
    s = _fresh()
    out = bridge.navila_get_ego_frame()
    assert isinstance(out, list) and len(out) == 1
    assert json.loads(out[0].text)["ok"] is False


# ---------------------------------------------------------------------------
# 8d. navila_nudge -- Orchestrator-issued raw motion, through the same veto +
#     watchdog gate as navigate_step. Escape hatch for the frozen-frame livelock.
# ---------------------------------------------------------------------------

def test_nudge_errors_when_idle():
    s = _fresh()
    r = s.nudge(wz=0.5)
    assert r["ok"] is False
    assert "no active episode" in r["error"]


def test_nudge_executes_a_turn_through_the_gate():
    s = _fresh()
    s.clear_hazards()
    try:
        s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=8)
        r = s.nudge(wz=0.5, duration_s=1.0, reason="turn toward clear space")
    finally:
        s.clear_hazards()

    assert r["ok"] is True
    assert r["source"] == "orchestrator_nudge"
    assert abs(r["yaw_delta_deg"]) > 10.0     # actually turned
    assert r["moved_m"] < 0.05                # a pure turn, no translation
    assert r["executed_ticks"] > 0
    assert r["done"] is False
    assert r["nudge_count"] == 1
    assert r["decision_index"] == 1           # ticks the budget as a backstop
    assert r.get("vetoed") is not True


def test_nudge_rejects_out_of_bounds_and_all_zero():
    s = _fresh()
    s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=5)
    assert s.nudge(wz=99.0)["ok"] is False        # yaw rate too high
    assert s.nudge(vx=5.0)["ok"] is False         # linear speed too high
    assert s.nudge(wz=0.5, duration_s=0.0)["ok"] is False
    assert s.nudge(wz=0.5, duration_s=99.0)["ok"] is False
    assert s.nudge(vx=0.0, vy=0.0, wz=0.0)["ok"] is False  # nothing to do
    assert s.status()["nudge_count"] == 0        # none of those executed


def test_nudge_blocked_by_veto_terminal_ends_episode():
    s = _fresh()
    s.clear_hazards()
    try:
        s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=5)
        s.inject_hazard(at_step=1)
        r = s.nudge(wz=0.5, duration_s=1.0)
    finally:
        s.clear_hazards()

    assert r["source"] == "orchestrator_nudge"
    assert r["vetoed"] is True
    assert r["done"] is True
    assert r["termination_reason"] == "veto"
    assert r["control_steps"] == 0             # gated before any physics


def test_nudge_blocked_by_veto_advisory_keeps_episode_running():
    s = _fresh()
    s.clear_hazards()
    try:
        s.start_episode(
            "go",
            vlm_script="move forward by 75 cm; stop",
            max_decisions=5,
            veto_mode="advisory",
        )
        s.inject_hazard(at_step=1, duration_steps=1)
        r = s.nudge(wz=0.5, duration_s=1.0)
    finally:
        s.clear_hazards()

    assert r["source"] == "orchestrator_nudge"
    assert r["vetoed"] is True
    assert r["done"] is False
    assert r["advisory_veto_count"] == 1
    assert r["control_steps"] == 0


def test_nudge_counts_toward_max_decisions():
    s = _fresh()
    s.clear_hazards()
    try:
        s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=2)
        first = s.nudge(wz=0.3, duration_s=0.5)
        second = s.nudge(wz=0.3, duration_s=0.5)
    finally:
        s.clear_hazards()

    assert first["done"] is False
    assert second["done"] is True
    assert second["termination_reason"] == "max_decisions"


def test_nudge_respects_a_pre_existing_emergency_stop():
    s = _fresh()
    s.start_episode("go far", vlm_script="move forward by 75 cm")
    s.backend.interrupted = True  # watchdog fired out of band

    r = s.nudge(wz=0.5)
    assert r["ok"] is True
    assert r["phase"] == "stopped"
    assert r["termination_reason"] == "emergency_stop"
    assert "interrupted" in r["note"]


def test_navila_nudge_tool_wraps_session():
    s = _fresh()
    assert bridge.navila_nudge(wz=0.3)["ok"] is False  # idle
    s.start_episode("go", vlm_script="move forward by 75 cm; stop", max_decisions=5)
    r = bridge.navila_nudge(wz=0.3, duration_s=0.5, reason="probe right")
    assert r["ok"] is True and r["source"] == "orchestrator_nudge"
    json.dumps(r)  # tool output must stay JSON-safe


def test_three_consecutive_nudges_terminate_as_a_dead_end():
    s = _fresh()
    s.clear_hazards()
    try:
        s.start_episode("go", vlm_script="stop", max_decisions=20)
        first = s.nudge(wz=0.3, duration_s=0.5)
        second = s.nudge(wz=0.3, duration_s=0.5)
        third = s.nudge(wz=0.3, duration_s=0.5)
    finally:
        s.clear_hazards()

    assert first["consecutive_nudges"] == 1 and first["done"] is False
    assert second["consecutive_nudges"] == 2 and second["done"] is False
    assert third["done"] is True
    assert third["termination_reason"] == "nudge_deadlock"
    assert third["consecutive_nudges"] == bridge._MAX_CONSECUTIVE_NUDGES
    # the 3rd nudge terminates without running any physics
    assert third["control_steps"] == second["control_steps"]
    assert "dead-end" in third["note"] or "dead end" in third["note"].lower()


def test_autonomous_navigate_step_resets_the_nudge_streak():
    s = _fresh()
    s.clear_hazards()
    try:
        s.start_episode(
            "go",
            vlm_script="move forward by 50 cm; move forward by 50 cm; stop",
            max_decisions=20,
        )
        s.nudge(wz=0.3, duration_s=0.5)
        s.nudge(wz=0.3, duration_s=0.5)
        assert s.status()["consecutive_nudges"] == 2

        step = s.navigate_step()
        assert step["executed_ticks"] > 0
        assert step.get("vetoed") is not True
        assert s.status()["consecutive_nudges"] == 0

        # streak restarted -> two more nudges do NOT hit the dead-end guard
        again = s.nudge(wz=0.3, duration_s=0.5)
        assert again["done"] is False
        assert again["consecutive_nudges"] == 1
    finally:
        s.clear_hazards()


def test_vetoed_navigate_step_does_not_reset_the_nudge_streak():
    s = _fresh()
    s.clear_hazards()
    try:
        s.start_episode(
            "go",
            vlm_script="move forward by 50 cm; move forward by 50 cm; stop",
            max_decisions=20,
            veto_mode="advisory",
        )
        s.nudge(wz=0.3, duration_s=0.5)   # decision 1, veto frame step 1
        s.nudge(wz=0.3, duration_s=0.5)   # decision 2, veto frame step 2

        # hazard active only on decision 3 -- the navigate_step, not the nudges
        s.inject_hazard(at_step=3, duration_steps=2)
        cs_before = s.status()["control_steps"]
        step = s.navigate_step()
        assert step["vetoed"] is True
        assert step["control_steps"] == cs_before   # gated before any physics
        # zero physics -> streak untouched, so the next nudge is the 3rd and trips
        third = s.nudge(wz=0.3, duration_s=0.5)
        assert third["done"] is True
        assert third["termination_reason"] == "nudge_deadlock"
    finally:
        s.clear_hazards()


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


# ---------------------------------------------------------------------------
# C2 camera-capture-only fallback (docs/PLAN.md, "C" item 3): _capture_frame
# prefers a real backend.capture_frame() over the mock placeholder, and never
# lets a capture failure block the loop.
# ---------------------------------------------------------------------------

class _CameraBackend:
    """Fake StepBackend exposing capture_frame(), no other StepBackend surface
    needed since _capture_frame only ever does getattr(backend, "capture_frame")."""

    def __init__(self, frame):
        self._frame = frame

    def capture_frame(self):
        return self._frame


class _RaisingCameraBackend:
    def capture_frame(self):
        raise RuntimeError("simulated capture failure")


class _NoneCameraBackend:
    def capture_frame(self):
        return None


def test_capture_frame_prefers_real_backend_frame():
    s = _fresh()
    deps = bridge._load_perstep()
    real = np.full((64, 64, 3), 42, dtype=np.uint8)
    s.backend = _CameraBackend(real)
    frame = s._capture_frame(deps)
    assert frame.shape == (64, 64, 3)
    assert (frame == 42).all()


def test_capture_frame_falls_back_when_backend_has_no_camera():
    s = _fresh()
    deps = bridge._load_perstep()
    s.backend = object()  # no capture_frame attribute at all
    frame = s._capture_frame(deps)
    assert frame.shape == (8, 8, 3)


def test_capture_frame_falls_back_when_camera_returns_none():
    s = _fresh()
    deps = bridge._load_perstep()
    s.backend = _NoneCameraBackend()
    frame = s._capture_frame(deps)
    assert frame.shape == (8, 8, 3)


def test_capture_frame_falls_back_when_camera_raises():
    s = _fresh()
    deps = bridge._load_perstep()
    s.backend = _RaisingCameraBackend()
    frame = s._capture_frame(deps)  # must not propagate the RuntimeError
    assert frame.shape == (8, 8, 3)


def test_mock_backend_episode_still_uses_placeholder_frames():
    """Regression guard: MockBackend has no capture_frame, so start_episode /
    navigate_step must keep recording 8x8 placeholders exactly as before this
    change -- the whole point of getattr(..., "capture_frame", None)."""
    s = _fresh()
    s.start_episode("go", vlm_script="move forward by 50 cm; stop", max_decisions=5)
    assert s._frames[0].shape == (8, 8, 3)
    s.navigate_step()
    assert all(f.shape == (8, 8, 3) for f in s._frames)


# ---------------------------------------------------------------------------
# navila_trigger_scene_hazard (docs/PLAN.md "C" item 5): in-scene hazard
# trigger, independent of any episode/backend. bridge_backends.trigger_scene_
# hazard itself is exercised against a fake edit service in
# test_bridge_backends.py; these tests only cover _trigger_scene_hazard_impl's
# own validation + error-shape contract, via a monkeypatched deps entry.
# ---------------------------------------------------------------------------

def _with_patched_trigger(fake_fn, fn):
    deps = bridge._load_perstep()
    orig = deps.get("trigger_scene_hazard")
    deps["trigger_scene_hazard"] = fake_fn
    try:
        fn()
    finally:
        deps["trigger_scene_hazard"] = orig


def test_trigger_scene_hazard_rejects_empty_actor_name():
    result = bridge._trigger_scene_hazard_impl("", 0.0, 0.0, 0.0)
    assert result["ok"] is False


def test_trigger_scene_hazard_rejects_whitespace_actor_name():
    result = bridge._trigger_scene_hazard_impl("   ", 0.0, 0.0, 0.0)
    assert result["ok"] is False


def test_trigger_scene_hazard_success_reports_pose_and_forwards_args():
    calls = []

    def _check():
        result = bridge._trigger_scene_hazard_impl(
            "blue_hatchback_car_1", 1.0, 2.0, 0.0, yaw_deg=90.0
        )
        assert result["ok"] is True
        assert result["actor_name"] == "blue_hatchback_car_1"
        assert result["position"] == {"x": 1.0, "y": 2.0, "z": 0.0}
        assert result["yaw_deg"] == 90.0
        assert len(calls) == 1
        actor, pos, rot = calls[0]
        assert actor == "blue_hatchback_car_1"
        assert pos == (1.0, 2.0, 0.0)
        # 90 deg yaw -> wxyz quat (cos45, 0, 0, sin45)
        assert math.isclose(rot[0], math.cos(math.pi / 4), rel_tol=1e-9)
        assert math.isclose(rot[3], math.sin(math.pi / 4), rel_tol=1e-9)

    _with_patched_trigger(
        lambda actor, pos, rot=(1.0, 0.0, 0.0, 0.0): calls.append((actor, pos, rot)),
        _check,
    )


def test_trigger_scene_hazard_failure_is_reported_not_raised():
    def _boom(actor, pos, rot=(1.0, 0.0, 0.0, 0.0)):
        raise RuntimeError("OrcaLab edit service not reachable at 127.0.0.1:50151")

    def _check():
        result = bridge._trigger_scene_hazard_impl("blue_hatchback_car_1", 0.0, 0.0, 0.0)
        assert result["ok"] is False
        assert "not reachable" in result["error"]

    _with_patched_trigger(_boom, _check)


def test_trigger_scene_hazard_impl_result_survives_json_dumps():
    def _check():
        result = bridge._trigger_scene_hazard_impl("blue_hatchback_car_1", 0.0, 0.0, 0.0)
        bridge._dumps(result)  # must not raise

    _with_patched_trigger(lambda actor, pos, rot=(1.0, 0.0, 0.0, 0.0): None, _check)


# ---------------------------------------------------------------------------
# navila_reset_scene_layout (docs/PLAN.md "C" item 6): scene reset
# reliability, independent of any episode/backend. bridge_backends.
# reset_scene_layout itself is exercised against a fake edit service in
# test_bridge_backends.py; these tests only cover _reset_scene_layout_impl's
# own arg-parsing + error-shape contract, via a monkeypatched deps entry.
# ---------------------------------------------------------------------------

def _with_patched_reset(fake_fn, fn):
    deps = bridge._load_perstep()
    orig = deps.get("reset_scene_layout")
    deps["reset_scene_layout"] = fake_fn
    try:
        fn()
    finally:
        deps["reset_scene_layout"] = orig


def test_reset_scene_layout_impl_full_reset_passes_none_actor_names():
    calls = []

    def _check():
        result = bridge._reset_scene_layout_impl(None)
        assert result["ok"] is True
        assert result["restored_actors"] == ["blue_hatchback_car_1", "traffic_light_1"]
        assert result["count"] == 2
        assert calls == [None]

    _with_patched_reset(
        lambda actor_names=None: calls.append(actor_names)
        or ["blue_hatchback_car_1", "traffic_light_1"],
        _check,
    )


def test_reset_scene_layout_impl_splits_semicolon_actor_names():
    calls = []

    def _check():
        result = bridge._reset_scene_layout_impl("blue_hatchback_car_1; traffic_light_1 ")
        assert result["ok"] is True
        assert calls == [["blue_hatchback_car_1", "traffic_light_1"]]

    _with_patched_reset(
        lambda actor_names=None: calls.append(actor_names) or list(actor_names),
        _check,
    )


def test_reset_scene_layout_impl_blank_actor_names_means_full_reset():
    calls = []

    def _check():
        bridge._reset_scene_layout_impl("   ")
        assert calls == [None]

    _with_patched_reset(lambda actor_names=None: calls.append(actor_names) or [], _check)


def test_reset_scene_layout_impl_failure_is_reported_not_raised():
    def _boom(actor_names=None):
        raise KeyError("['nonexistent_actor']")

    def _check():
        result = bridge._reset_scene_layout_impl("nonexistent_actor")
        assert result["ok"] is False
        assert "nonexistent_actor" in result["error"]

    _with_patched_reset(_boom, _check)


def test_reset_scene_layout_impl_result_survives_json_dumps():
    def _check():
        result = bridge._reset_scene_layout_impl(None)
        bridge._dumps(result)  # must not raise

    _with_patched_reset(lambda actor_names=None: ["a1"], _check)


def test_spawn_camera_impl_rejects_blank_actor_name():
    r = bridge._spawn_camera_impl(actor_name="   ")
    assert r["ok"] is False
    bridge._dumps(r)


def test_spawn_camera_impl_forwards_args_and_wraps_result():
    calls = {}

    def _fake(actor_name, asset_path, position, *, replace):
        calls["args"] = (actor_name, asset_path, position, replace)
        return {"actor_name": actor_name, "asset_path": asset_path, "created": True}

    deps = bridge._load_perstep()
    orig = deps.get("spawn_camera_actor")
    deps["spawn_camera_actor"] = _fake
    try:
        r = bridge._spawn_camera_impl(x=0.2, y=0.0, z=0.4, replace=True)
    finally:
        if orig is not None:
            deps["spawn_camera_actor"] = orig
        else:
            deps.pop("spawn_camera_actor", None)
    assert r["ok"] is True and r["created"] is True
    assert calls["args"] == ("mujococamera1080", "prefabs/mujococamera1080", (0.2, 0.0, 0.4), True)
    bridge._dumps(r)


def test_spawn_camera_impl_reports_backend_failure_without_raising():
    def _boom(*a, **k):
        raise RuntimeError("OrcaLab edit service not reachable at 127.0.0.1:50151")

    deps = bridge._load_perstep()
    orig = deps.get("spawn_camera_actor")
    deps["spawn_camera_actor"] = _boom
    try:
        r = bridge._spawn_camera_impl()
    finally:
        if orig is not None:
            deps["spawn_camera_actor"] = orig
        else:
            deps.pop("spawn_camera_actor", None)
    assert r["ok"] is False
    assert "not reachable" in r["error"]
    bridge._dumps(r)


# ---------------------------------------------------------------------------
# 11. Live judge-facing status feed (navila_get_live_status)
# ---------------------------------------------------------------------------

def _all_text(status: dict) -> str:
    return "\n".join(status["new_lines"])


def test_live_status_traces_orchestrator_and_driver_each_decision():
    s = _fresh()
    s.clear_hazards()
    s.start_episode(
        "walk forward down the street",
        vlm_script="move forward by 75 cm; stop",
        max_decisions=5,
    )
    s.navigate_step()
    status = bridge.navila_get_live_status()
    text = _all_text(status)
    assert status["ok"] is True
    assert "EPISODE START" in text
    assert "Orchestrator -> NaVILA: 'walk forward down the street'" in text
    assert "NaVILA decided: 'move forward by 75 cm'" in text
    assert "robot command: vx=" in text
    assert "perception:" in text  # frame described (placeholder here)
    assert status["status_line"] == "Status: CLEAR - Navigating"
    assert status["active_alert"] is None


def test_live_status_since_seq_returns_only_new_lines():
    s = _fresh()
    s.clear_hazards()
    s.start_episode("go", vlm_script="move forward by 75 cm; move forward by 75 cm; stop")
    s.navigate_step()
    first = bridge.navila_get_live_status()
    s.navigate_step()
    second = bridge.navila_get_live_status(since_seq=first["next_seq"])
    assert second["next_seq"] >= first["next_seq"]
    assert "decision 2" in _all_text(second)
    assert "decision 1" not in _all_text(second)


def test_live_status_emits_clear_heartbeat_between_steps():
    s = _fresh()
    s.clear_hazards()
    s.start_episode("go", vlm_script="move forward by 75 cm; move forward by 75 cm; stop")
    s.navigate_step()
    assert "Status: CLEAR - Navigating" in _all_text(bridge.navila_get_live_status())


def test_live_status_veto_raises_visible_banner_with_reason():
    s = _fresh()
    s.clear_hazards()
    s.start_episode("cross", vlm_script="move forward by 75 cm; stop", max_decisions=5)
    s.inject_hazard(at_step=1)
    s.navigate_step()
    status = bridge.navila_get_live_status()
    text = _all_text(status)
    assert "[VETO:" in text
    assert "!!! " in text  # the loud banner rule
    assert status["active_alert"] is not None and status["active_alert"].startswith("[VETO:")
    assert status["status_line"].startswith("Status: HALTED")
    # still hooked into A's DecisionLogbook
    assert any(e["kind"] == "VETO" for e in status["logbook_tail"])


def test_live_status_watchdog_trip_raises_emergency_banner():
    s = _fresh()
    s.clear_hazards()
    s.clear_force_drops()  # _force_events persists across _fresh() by design
    try:
        s.start_episode(
            "walk",
            vlm_script="move forward by 75 cm; move forward by 75 cm; stop",
            max_decisions=5,
            watchdog_debounce_ticks=3,
        )
        s.inject_force_drop(at_step=5, duration_steps=20)
        s.navigate_step()
        status = bridge.navila_get_live_status()
        text = _all_text(status)
        assert "[EMERGENCY STOP:" in text
        assert status["active_alert"].startswith("[EMERGENCY STOP:")
        assert status["status_line"].startswith("Status: HALTED")
    finally:
        s.clear_force_drops()  # don't leak the schedule into the next test


def test_live_status_clear_stop_lifts_the_alert():
    s = _fresh()
    s.clear_hazards()
    s.clear_force_drops()
    try:
        s.start_episode(
            "walk",
            vlm_script=(
                "move forward by 75 cm; move forward by 75 cm; "
                "move forward by 75 cm; stop"
            ),
            max_decisions=8,
            watchdog_debounce_ticks=3,
        )
        s.inject_force_drop(at_step=5, duration_steps=8)
        s.navigate_step()  # trips
        assert bridge.navila_get_live_status()["active_alert"] is not None
        s.clear_force_drops()
        s.clear_stop()
        status = bridge.navila_get_live_status()
        assert status["active_alert"] is None
        assert status["status_line"] == "Status: CLEAR - Navigating"
    finally:
        s.clear_force_drops()


def test_live_status_driver_parse_failure_is_a_visible_fault():
    s = _fresh()
    s.clear_hazards()
    s.start_episode("x", vlm_script="go somewhere vaguely")
    s.navigate_step()
    text = _all_text(bridge.navila_get_live_status())
    assert "DRIVER FAULT" in text


def test_live_status_idle_before_any_episode():
    _fresh()
    status = bridge.navila_get_live_status()
    assert status["ok"] is True
    assert status["status_line"].startswith("Status: IDLE")


def test_live_monitor_default_is_best_effort_and_never_breaks_start():
    s = _fresh()
    s.clear_hazards()
    # Default (arg + env both unset): best-effort attempt. In the test env cv2
    # isn't the GUI build, so it degrades -- but the episode MUST still start and
    # the field MUST report a state, not crash.
    r = s.start_episode("go", vlm_script="stop")
    assert r["ok"] is True
    assert isinstance(r["live_monitor"], str) and r["live_monitor"]


def test_live_monitor_explicitly_off_reports_off():
    s = _fresh()
    s.clear_hazards()
    r = s.start_episode("go", vlm_script="stop", live_monitor=False)
    assert r["ok"] is True
    assert r["live_monitor"] == "off"


def test_live_monitor_requested_but_unavailable_degrades_cleanly():
    s = _fresh()
    s.clear_hazards()
    # cv2 isn't the GUI build in the test env -> construction fails; explicit
    # request -> a warn line, but the episode must still start.
    r = s.start_episode("go", vlm_script="stop", live_monitor=True)
    assert r["ok"] is True
    assert r["live_monitor"] == "on" or "unavailable" in r["live_monitor"]


def test_live_monitor_selftest_impl_reports_structured_diagnosis():
    out = bridge._live_monitor_selftest_impl(keep_open=False)
    bridge._dumps(out)  # must not raise
    assert set(out) >= {"ok", "opened", "display", "server_python"}
    # cv2 in the test env is either absent or headless -> not a clean open, but
    # the report must be well-formed either way.
    assert out["ok"] in (True, False)
    if not out["ok"]:
        assert "error" in out


class _FakeMonitorThread:
    """Stand-in for _MonitorThread so the session-scoped lifecycle can be tested
    without a real cv2 GUI window."""

    def __init__(self) -> None:
        self.error = None
        self.stopped = False
        self.frames = []

    def ok(self):
        return not self.stopped

    def submit(self, frame, **kwargs):
        self.frames.append(kwargs.get("status"))

    def stop(self):
        self.stopped = True


def test_live_monitor_thread_is_session_scoped_and_survives_episode_close():
    s = _fresh()
    s.clear_hazards()
    fake = _FakeMonitorThread()
    s._monitor_thread = fake
    s.start_episode("go", vlm_script="stop")  # default -> reuse existing thread
    assert s._monitor_thread is fake and not fake.stopped
    s.navigate_step()
    s.close()  # episode close must NOT tear the window down
    assert s._monitor_thread is fake and not fake.stopped
    s._monitor_thread = None  # don't leak the fake into later tests


def test_live_monitor_explicit_false_tears_the_thread_down():
    s = _fresh()
    s.clear_hazards()
    fake = _FakeMonitorThread()
    s._monitor_thread = fake
    s.start_episode("go", vlm_script="stop", live_monitor=False)
    assert fake.stopped is True
    assert s._monitor_thread is None
    assert s.status()["live_monitor"] == "off"


def test_navila_get_live_status_return_survives_json_dumps():
    s = _fresh()
    s.clear_hazards()
    s.start_episode("go", vlm_script="move forward by 75 cm; stop")
    s.navigate_step()
    bridge._dumps(bridge.navila_get_live_status())  # must not raise


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
