"""Compact positive-boundary prompts for staged traffic crossings."""

from __future__ import annotations

from .contracts import VelocityCommand


def premature_stop_recovery_command() -> VelocityCommand:
    """Return one canonical forward step that refreshes the crossing view."""

    return VelocityCommand(vx=0.5, vy=0.0, wz=0.0, duration_s=0.5)


def _label(value: str, name: str) -> str:
    label = str(value).strip()
    if not label:
        raise ValueError(f"{name} must be a non-empty waypoint label")
    return label


def traffic_light_crossing_waypoints(
    *,
    wait_waypoint: str = "curb-side waiting waypoint",
    center_waypoint: str = "center of the zebra crossing",
    exit_waypoint: str = "far-side exit waypoint",
) -> tuple[str, str, str]:
    """Return the three VLM states for a traffic-light crossing.

    Vehicle clearance is an invariant, so its positive spatial boundary is
    repeated in every state instead of being deferred to the final state.
    """

    wait = _label(wait_waypoint, "wait_waypoint")
    center = _label(center_waypoint, "center_waypoint")
    exit_ = _label(exit_waypoint, "exit_waypoint")
    clearance = (
        "Choose poses whose nearest visible vehicle surface remains at least "
        "2 meters from every side of the robot."
    )
    return (
        (
            f"Reach {wait}. Hold a stable stance there and visually identify the "
            f"two traffic-light poles, one on each side of the crossing entrance. "
            f"{clearance} Output stop when the waypoint is reached and both poles "
            "are identified."
        ),
        (
            f"Enter the white zebra-stripe corridor and advance to {center}. Keep "
            "the robot footprint inside the corridor bounded by the two outer "
            f"edges of the white stripes. {clearance} Output stop when the center "
            "waypoint is reached."
        ),
        (
            f"Continue through the white zebra-stripe corridor to {exit_}. Keep "
            "the robot footprint inside the corridor bounded by the two outer "
            f"edges of the white stripes. {clearance} Output stop after the full "
            "robot footprint reaches the exit waypoint."
        ),
    )
