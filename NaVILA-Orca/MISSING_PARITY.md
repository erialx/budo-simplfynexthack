# 与原版 NaVILA-Bench 对齐还缺什么

结论：当前已经跑通的是系统链路，不是原版场景质量。`scene_fidelity=false` 和
`--scene-ready` 的保护应继续保留。

## 已经具备

- episode 读取、NaVILA 八帧/TCP、动作解析和 200/50 Hz 时序。
- Go2 MJWarp 推理、OrcaGym `UpdateLocalEnv` 位姿桥和结果写盘。
- 512×512 Go2 ego RGB：`/navila_ego` 按 base pose 刚性跟随，前向光轴和实际 transform 已验证；当前 engine build 使用 `grpc-png` 拉帧。
- 六项导航指标的计算代码。

## 仍缺的 P0

1. **真实场景、碰撞和坐标**
   - Matterport 源资产其实已经在 `components/NaVILA-Bench` 中；原版使用 textured USD mesh，
     并不是必须先有 3DGS。
   - 仍需把每个 scene 转成 Orca 可视资产，并从同一份几何生成 MJWarp collision。
   - 仍需完成 dataset、MJWarp、Orca render world 的坐标标定，以及 episode start/goal/path reset。
   - 当前手工摆放物体的 smoke 只复用 OrcaLab combined XML 做映射/渲染；这些物体尚未
     合并进本地 MJWarp，不能把“画面中有物体”当作“Go2 已与物体发生物理碰撞”。

2. **原版低层 locomotion 观测（仅严格复现原 checkpoint 时需要）**
   - 这项与高层 VLM/ego RGB 无关；当前普通 47D locomotion 已足够验证相机与 VLN pipeline。
   - 当前运行的是 `Unitree-Go2-Flat`，actor 输入只有 47D，只能作为平地 smoke。
   - 原版 checkpoint 的 actor 输入是 909D：45D 当前本体观测 + 459D
     （17×27）LiDAR 高度图 + 9×45D 本体历史。
   - 仍需实现 MJWarp terrain raycast、原版 voxel/坐标变换、history reset/update、
     observation 顺序和 joint/action 顺序校验。
   - 本机已有 `Unitree-Go2-Rough` 234D checkpoint，可先用于碰撞体联调，但它仍不等于
     原版 909D policy。

3. **动力学与 episode 行为**
   - 需要对齐 PD、effort、action delay、接触和关节符号。
   - 需要补原版的 reset 后 100 tick warmup、stuck 1000 tick、跌倒/姿态终止和
     episode tick 上限。

4. **原格式评测输出**
   - 每集独立 `measurements/<index>.json`、1024×512/10 fps MP4。
   - batch/resume、失败隔离，以及 episode 0 与原版 golden trajectory/Success/SPL 回归。

## 深度图是否需要重新训练

要区分两条路线：

- **严格保持原版**：先做 909D LiDAR 高度图和 9 帧 history。原 benchmark 虽然定义了
  `depth_obs`，但实际 checkpoint 没走 depth-CNN；Go2 ego RGB 是给高层 VLM，terrain
  height map 是给低层 locomotion，它们不是同一路数据。
- **新增 perceptive locomotion**：可以参考
  [InstinctMJ](https://github.com/project-instinct/InstinctMJ) 的 MJWarp ray-cast depth camera、
  深度历史/延迟和 Conv2d encoder。其示例把多帧 18×32 depth 编码成 latent 再与
  proprio 拼接。该方案会改变 actor 结构，必须重新训练 Go2 12-action policy，不能直接
  使用当前 47D 或原版 909D checkpoint。

可直接看的参考实现：

- [depth camera 与预处理](https://github.com/project-instinct/InstinctMJ/blob/4ed2b32f8719ff9fc138708341031e935afda0d2/src/instinct_mj/tasks/parkour/config/g1/g1_parkour_target_amp_cfg.py#L317-L375)
- [depth history/延迟进入 policy](https://github.com/project-instinct/InstinctMJ/blob/4ed2b32f8719ff9fc138708341031e935afda0d2/src/instinct_mj/tasks/parkour/config/g1/g1_parkour_target_amp_cfg.py#L424-L487)
- [depth Conv2d encoder](https://github.com/project-instinct/InstinctMJ/blob/4ed2b32f8719ff9fc138708341031e935afda0d2/src/instinct_mj/tasks/parkour/config/g1/agents/instinct_rl_amp_cfg.py#L11-L42)
- [扫描 mesh 的视觉/碰撞分离](https://github.com/project-instinct/InstinctMJ/blob/4ed2b32f8719ff9fc138708341031e935afda0d2/src/instinct_mj/terrains/trimesh/mesh_terrains.py#L1609-L1657)

InstinctMJ 是 G1/29-action 参考，不能直接当 Go2 checkpoint 使用；若移植其代码，还需
单独检查 CC BY-NC 4.0 许可边界。

## 项目内组件入口

这些都是本机同一套项目组件。为保留现有未提交修改并避免重复几十 GB 数据，当前在
`components/` 下建立本地链接：

```text
components/
├── NaVILA
├── NaVILA-Bench
├── OrcaLab
├── OrcaGym
├── OrcaLab-RSLRL
├── unitree_rl_mjlab
├── orca_rl
└── mjlab
```

运行代码已优先读取 `components/NaVILA-Bench`、`components/unitree_rl_mjlab`、
`components/orca_rl` 和 `components/OrcaLab-RSLRL`，原绝对路径仅作为兼容 fallback。
OrcaLab/OrcaGym/mjlab 的实际 Python 包仍使用已验证的 `orcalab` 环境版本。

## 建议顺序

1. 先做 episode 0 的 USD visual + 同源 collision + identity 坐标/reset。
2. 先用当前 47D 普通 locomotion 做真实场景和 ego RGB 联调。
3. 如需严格复现原版低层，再实现 909D observation；若改走 depth-CNN，则建立新的 Go2 训练任务并重训。
4. 最后补 termination、原格式视频/JSON、episode 0 golden 和 batch/resume。

只有视觉、碰撞、坐标/reset、ego RGB 和低层观测都通过后，才允许
`--scene-ready`/`scene_fidelity=true`。
