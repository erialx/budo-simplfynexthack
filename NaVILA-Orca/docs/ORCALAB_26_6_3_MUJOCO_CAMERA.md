<p align="right"><sub><strong>English</strong> · <a href="ORCALAB_26_6_3_MUJOCO_CAMERA_zh.md">中文</a></sub></p>

# OrcaLab 26.6.3 persistent MuJoCo camera

NaVILA-Orca pins `orca-lab==26.6.3` and `orca-gym==26.6.3`. Ego RGB does not use `prefabs/agentcamera`: when created by the public Edit RPC in 26.6.3, that asset does not expose the legacy `IsRecording` / `ColorCamera` properties.

The runtime uses `prefabs/mujococamera1080` instead. It creates the actor once, writes its head world pose with `SetActorTransform` after every Go2 state update, then reads RGB with `GetCameraPNG`. The actor is deleted only when the episode ends.

This path has been verified in the 26.6.3 GUI: the same `mujococamera1080` actor can complete two successive `GetCameraPNG` calls; both files are valid and their contents differ after an intervening `SetActorTransform`.

Install:

```bash
python -m pip install --upgrade --force-reinstall \
  'orca-lab==26.6.3' 'orca-gym==26.6.3'
```

After upgrading, quit and restart the OrcaLab GUI, then run:

```bash
./scripts/run_orcalab_camera_smoke.sh
./scripts/run_orcalab_scene_locomotion.sh
```
