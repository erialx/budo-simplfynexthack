# NaVILA-Orca Project Handover & Context Summary

## ⚠️ Crucial Note for Your Claude Integration (The Macro/Micro Architecture)
Even though your Claude agent will be dynamically generating the text instructions (replacing my hardcoded waypoints), you **must** use my updated `traffic_crossing.py` file to run the simulation. 

Think of it like this:
*   **Claude is the Navigator (Macro Brain):** It decides the high-level goal and tells the system where to go (e.g., "Walk to the cardboard box").
*   **`traffic_crossing.py` is the Driver's Reflexes (Micro Brain):** It takes Claude's directions, talks to the physics engine, and stops the robot from crashing.

**Why you need this file:** 
During testing, we discovered a "visual deadlock" bug. Sometimes, when given a prompt, the local vision model gets confused for a split second and yells "Stop!". Without my updated file, the robot just stands perfectly still. Because it doesn't move, its camera view never changes. It just looks at the exact same frozen picture and yells "Stop!" forever, completely ignoring Claude's overall instructions. 

I added a `WAYPOINT_STOP_OVERRIDE` safety reflex to `traffic_crossing.py`. Now, if the robot tries to freeze prematurely, this code overrides it and physically forces the dog to take a step forward. This changes the camera view, breaks the "freeze" loop, and forces the AI to look at its surroundings again so it can finish Claude's mission. 

*(Note: You can delete the hardcoded 3-stage crosswalk sequence inside the file to make it open-ended for Claude, but **do not delete the `WAYPOINT_STOP_OVERRIDE` logic**, or your agent will get trapped in an infinite loop!)*

---

## System Context for Orchestration Agent
*   **Environment:** OrcaLab (Version 26.7.1) running on Windows using the `(orcalab)` Conda environment.
*   **Compute:** Falling back to CPU-only execution (`--device cpu`) due to local CUDA constraints.
*   **Core Architecture:** Navila-Orca quadruped robot navigation driven by a Vision-Language Model (VLM).
*   **Integration Goal:** Transition from static terminal commands to a dynamic, GenAI-driven orchestration layer (Claude) that injects generated hazard frames (e.g., red lights, moving cars) into the VLM's camera feed to test collision avoidance.

## Task 1: Environment & Physics Stabilization
*   **Issue:** Default shell launcher scripts caused Conda pathing errors, Windows text-encoding crashes, and "ghost dog" visual freezing because the physics outpaced the 3D renderer.
*   **Resolution:** Bypassed the `.sh` script to run the Python CLI directly.
*   **Implementation:** Forced UTF-8 encoding (`python -X utf8`), enabled CPU rendering, and locked the physics-to-graphics frame rate using `--realtime-visual-sync`. The OrcaLab 3D window now successfully renders the POV camera and scene physics in real-time.

## Task 2: State Machine & Safety Logic (File Updated)
*   **Issue:** The visual deadlocks mentioned above.
*   **Resolution:** Modified `traffic_crossing.py` to enforce strict motion minimums.
*   **Implementation:** Added the `WAYPOINT_STOP_OVERRIDE` function. It forces a forward velocity (`vx=0.5m/s`) for 0.5s if a premature stop is predicted, ensuring the camera frame updates.

## Task 3: Real VLM Integration & Spatial Prompting
*   **Issue:** The robot was executing a hardcoded sequence (Walk, Turn, Stop) because it defaulted to a dummy tester script instead of the real neural network.
*   **Resolution:** Connected the runtime directly to the active NaVILA VLM backend via local TCP.
*   **Implementation:** Discovered that positive semantic spatial constraints work best. By instructing the model to "Maintain a strict 1-meter safety boundary," the robot successfully navigates 3.4+ meters and brakes smoothly before reaching physical hazards. 

## Execution Command (Live Demo Config)
To run the fully stabilized pipeline, ensure the OrcaLab 3D window is open and execute the following command. Wait for the `READY FOR DEMO` terminal prompt, press Enter, and then click **Play** in the OrcaLab UI.

```bash
python -X utf8 -m navila_orca.cli run \
  --device cpu \
  --render-backend orcalab \
  --orcagym-address 127.0.0.1:50051 \
  --orcalab-edit-address 127.0.0.1:50151 \
  --orcalab-camera-mode mujoco-png \
  --camera-transport grpc-png \
  --camera-actor-name mujococamera1080 \
  --camera-asset-path prefabs/mujococamera1080 \
  --robot-actor-name auto \
  --anchor-existing-scene \
  --no-publish \
  --strict-scene-alignment \
  --manual-xml-override \
  --live-monitor \
  --monitor-interval 0.1 \
  --state-stream-interval 0.04 \
  --realtime-visual-sync \
  --rehearsal \
  --traffic-light-crossing \
  --vlm-backend tcp \
  --vlm-host 127.0.0.1 \
  --vlm-port 54321 \
  --traffic-wait-waypoint "the very beginning of the white zebra stripes on the asphalt" \
  --traffic-center-waypoint "middle pair of white zebra stripes" \
  --traffic-exit-waypoint "the cardboard box on the far sidewalk; walk toward the box but you MUST STOP exactly 1 meter before reaching it. Maintain a strict 1-meter safety boundary and do not touch the box."