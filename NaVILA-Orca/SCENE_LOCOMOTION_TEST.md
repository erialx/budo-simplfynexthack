# 当前 OrcaLab 物体场景 locomotion 测试

这个测试复用已经打开的 OrcaLab 和当前 layout，不启动第二个 OrcaLab，也不调用
`publish_scene()`，所以不会清空已经摆好的油桶、柜子和垃圾桶。缺少的 `navila_ego`
相机会被创建，已有同名相机则直接复用。

## 运行前只做两项确认

1. 当前 layout 中放入且只放入一个完整 Go2 prefab；actor 名称不限。程序按全部 12 个
   Go2 关节自动识别，不能把 level camera、单个 mesh 或不完整的机器人当作 Go2。
2. OrcaLab 使用 no-simulation/external 模式，`127.0.0.1:50051` 和
   `127.0.0.1:50151` 可用。不要再启动另一个 OrcaLab。

当前检查到的 OrcaStudio 缓存
`/home/user/Orca/OrcaStudio/{3DB8A56E-2458-4543-93A1-1A41756B97DA}/tmp/out.xml`
只有 ground 和 ActorManipulator：`nq=7`、`nu=0`，还没有 Go2，也没有三个物体的
MuJoCo collision geom。若运行时下载的 XML 仍是这个内容，程序会在运动前直接报错；
先让 OrcaLab 重新编译当前 layout。

## 执行

```bash
cd /home/user/VLN/NaVILA-Orca
./scripts/run_orcalab_scene_locomotion.sh
```

脚本使用普通 `Unitree-Go2-Flat` locomotion：前进 50 cm，再 stop。它固定采用：

- 训练侧 MuJoCo profile：`timestep=0.005`、`ImplicitFast`、solver
  `iterations/ls_iterations/ccd_iterations = 10/20/50`、重力 `-9.81`，关闭流体阻力；
  ground 同时固定为 `friction=1/0.005/0.0001`、`solref=0.02/1`、
  `solimp=0.9/0.95/0.001/0.5/2`、`condim=3`；
- 下载 XML 只读保留，修正后的副本写到
  `outputs/scene_locomotion_smoke/scene_alignment/aligned_scene.xml`；
- qpos 按 12 个关节名字映射，不依赖 XML 中的关节/actuator 排列；
- 第一个本地 Go2 root 作为参考，将后续位移应用到 layout 中 Go2 原有 XY/yaw，ego
  camera 使用同一个 root 变换；
- reset/推力/摩擦等训练随机化关闭，便于复现。

预置导航 prompt 在 `prompts/orcalab_scene_locomotion.txt`：

```text
Move straight toward the orange rectangular cabinet between the blue barrel and the orange trash bin, then stop before reaching it.
```

默认 smoke 使用 scripted action，因此 prompt 会进入 episode 和结果文件，但不会改变
`move forward 50 cm → stop`。NaVILA server 已启动时，可让真实 VLM 使用同一个 prompt：

```bash
./scripts/run_orcalab_scene_locomotion.sh \
  --vlm-backend tcp \
  --vlm-host 127.0.0.1 \
  --vlm-port 54321
```

脚本的关键启动参数已经固定为：

```text
--render-backend orcalab
--orcagym-address 127.0.0.1:50051
--orcalab-edit-address 127.0.0.1:50151
--camera-transport grpc-png
--no-publish
--robot-actor-name auto
--anchor-existing-scene
--scene-profile mjlab-train
--strict-scene-alignment
--manual-xml-override
--instruction-file prompts/orcalab_scene_locomotion.txt
--image-interval 0.1
--state-stream-interval 0.04
--max-decisions 2
--max-control-steps 60
```

需要改地址或 VLM backend 时，把同名参数追加到脚本命令末尾，后面的值生效；
`--publish-scene` 无法和脚本内的 `--no-publish` 同时使用，避免误清当前 layout。

成功后检查：

```text
outputs/scene_locomotion_smoke/scene_alignment.json
outputs/scene_locomotion_smoke/measurements.json
outputs/scene_locomotion_smoke/frames/
```

`scene_alignment.json` 会记录原始 XML、覆盖 XML、识别出的 actor、qpos 映射、实际
MuJoCo 参数和非机器人 collision geom 数量。

本机当前是 OrcaLab/OrcaGym `26.5.1`、`mujoco-warp 3.5.0`、Python `mujoco 3.7.0`；
OrcaLocomotion 参考仓库把 Python `mujoco` 固定为 `3.5.0`。当前组合通过了 Go2
MJWarp 单步测试，但若目标是逐数值复现参考仓库，仍应另建 `mujoco 3.5.0` 环境，避免
直接降级并破坏现在可运行的 OrcaLab 环境。

## 这次测试的物理边界

该路径与 [OrcaLocomotion](https://github.com/openverse-orca/OrcaLocomotion) 的 replay
结构一致：Go2 动力学在本地 MJWarp，OrcaLab
combined XML 用于 actor 映射和渲染。远端物体不会自动进入本地 MJWarp，因此即使物体
在 OrcaLab 中可见，当前 Go2 仍可能穿过它。要测试真实碰撞，必须把同一批物体 collision
geom 合并进 MJLab/MJWarp 场景；仅修正 downloaded XML 的 `<option>` 和 ground contact
参数不会完成这一步。
