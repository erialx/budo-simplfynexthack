<p align="right"><sub><a href="ORCALAB_26_6_3_MUJOCO_CAMERA.md">English</a> · <strong>中文</strong></sub></p>

# OrcaLab 26.6.3 常驻 MuJoCo Camera

NaVILA-Orca 使用精确锁定的 `orca-lab==26.6.3` 和
`orca-gym==26.6.3`。Ego RGB 不使用 `prefabs/agentcamera`；该 asset 通过
26.6.3 的公开 Edit RPC 创建时不暴露旧的 `IsRecording` / `ColorCamera`
属性。

运行时改用 `prefabs/mujococamera1080`：创建一次、每次 Go2 状态更新后
通过 `SetActorTransform` 写入头部 world pose、再通过 `GetCameraPNG` 读取
RGB。任务结束时删除该 actor。

该路径已在 26.6.3 GUI 中实测：同一 `mujococamera1080` actor 可连续完成
两次 `GetCameraPNG`，两帧文件均有效且在中间 `SetActorTransform` 后内容
不同。

安装：

```bash
python -m pip install --upgrade --force-reinstall \
  'orca-lab==26.6.3' 'orca-gym==26.6.3'
```

升级后退出并重新启动 OrcaLab GUI，再运行：

```bash
./scripts/run_orcalab_camera_smoke.sh
./scripts/run_orcalab_scene_locomotion.sh
```
