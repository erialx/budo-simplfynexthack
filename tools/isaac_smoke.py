#!/usr/bin/env python3
"""Finite Isaac Sim / Isaac Lab smoke test for the local NaVILA setup."""

from omni.isaac.lab.app import AppLauncher


app_launcher = AppLauncher({"headless": True, "device_id": 0})
simulation_app = app_launcher.app

from omni.isaac.lab.sim import SimulationCfg, SimulationContext


def main() -> None:
    simulation = SimulationContext(SimulationCfg(dt=0.01, device="cuda:0"))
    simulation.reset()
    for _ in range(20):
        simulation.step()
    print("ISAAC_SMOKE_OK steps=20 device=cuda:0")


if __name__ == "__main__":
    try:
        main()
    finally:
        simulation_app.close()
