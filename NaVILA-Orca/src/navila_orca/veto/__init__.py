"""The Hazard Veto Agent and its fault-injection test harness.

See :mod:`navila_orca.veto.veto_agent` for the package docstring covering the
call-parse-gate design, and CLAUDE.md's "Fault injection" section for why
:class:`~navila_orca.veto.scenario_injector.ScenarioInjector` exists and how it
differs from the live-demo hazard trigger (an in-scene OrcaLab object or a
physical prop, not a frame overlay).
"""

from __future__ import annotations

from navila_orca.veto.scenario_injector import HazardInjectionEvent, ScenarioInjector
from navila_orca.veto.veto_agent import (
    HazardVetoAgent,
    VetoDecision,
    VetoParseError,
    VetoVisionClient,
    parse_veto_response,
)

__all__ = [
    "HazardInjectionEvent",
    "ScenarioInjector",
    "HazardVetoAgent",
    "VetoDecision",
    "VetoParseError",
    "VetoVisionClient",
    "parse_veto_response",
]
