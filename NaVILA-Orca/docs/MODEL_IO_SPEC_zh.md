<p align="right"><sub><a href="MODEL_IO_SPEC.md">English</a> · <strong>中文</strong></sub></p>

# Orca_VLN 模型 I/O 与采样规格

## 模型

本项目使用 **NaVILA** 作为高层视觉语言导航（VLN）模型。

NaVILA 根据自然语言任务和机器人第一视角图像判断下一步导航动作。它不直接
控制关节；文本动作会先转换为速度命令，再交给 Go2 低层运动策略执行。

```text
任务指令 + 8 帧 ego RGB
        → NaVILA
        → 导航动作
        → VelocityCommand(vx, vy, wz, duration)
        → Go2 低层策略
```

## 输入

每次 NaVILA 推理接收：

| 输入 | 规格 |
| --- | --- |
| 图像 | 恰好 8 帧、按时间排序的 RGB 图像 |
| 指令 | 一个非空 UTF-8 导航任务字符串 |
| 相机 | OrcaLab `prefabs/mujococamera1080` |
| 相机位置 | Go2 base 坐标系下 `(0.1, 0.0, 0.5) m` |
| 模型图像尺寸 | 当前 checkpoint 使用 `384×384` SigLIP 输入 |

TCP 请求格式：

```json
{
  "images": ["<base64 JPEG 1>", "...", "<base64 JPEG 8>"],
  "query": "<navigation instruction>"
}
```

### 八帧选择

- 相机历史帧每 `0.5 s` 仿真时间采集一次，即 `2 Hz`；
- 历史不足 8 帧时，在前面补黑帧；
- 历史较长时，从完整历史中均匀选择 7 帧，第 8 帧始终为最新帧；
- 当前实现是**全历史均匀采样**，不是最近 8 帧滚动窗口；
- 进入新 waypoint 后重新开始图像历史。

## 输出

NaVILA 每次返回一个文本导航动作。当前基线只接受以下动作：

| 模型输出 | 执行命令 |
| --- | --- |
| `move forward 25/50/75 cm` | `vx=0.5 m/s`，执行 `0.5/1.0/1.5 s` |
| `turn left 15/30/45 degrees` | `wz=+π/6 rad/s`，执行 `0.5/1.0/1.5 s` |
| `turn right 15/30/45 degrees` | `wz=-π/6 rad/s`，执行 `0.5/1.0/1.5 s` |
| `stop` | 速度和持续时间均为 0，结束任务 |

一次响应只能包含一个动作。未知距离、未知角度、空输出或多个动作都会被拒绝。
当前动作集合不包含侧移，`vy` 始终为 0。

模型生成使用确定性配置：

```text
do_sample=False
temperature=0
num_beams=1
max_new_tokens=512
```

## 采样与控制频率

| 环节 | 周期 | 频率 |
| --- | ---: | ---: |
| MuJoCo physics | `0.005 s` | `200 Hz` |
| Go2 低层策略 | `0.02 s` | `50 Hz` |
| OrcaLab 位姿与相机跟随更新 | `0.04 s` | `25 Hz` |
| NaVILA 历史图像采集 | `0.5 s` | `2 Hz` |
| Live monitor 刷新 | `0.1 s` | `10 Hz` |

NaVILA **不是固定频率调用**。系统执行完上一条 `0.5/1.0/1.5 s` 动作后，
才发起下一次推理。实际墙钟间隔还包含模型推理、图像传输和渲染耗时。

启动导航前，Go2 会以零速度运行 100 个低层控制步，即 2 秒仿真预热；随后
导航计时从 0 开始。

## 低层执行接口

高层与低层之间的固定接口为：

```text
VelocityCommand(vx, vy, wz, duration_s)
```

低层 Go2 policy 以 50 Hz 运行，接收包含 `[vx, vy, wz]` 在内的 47 维观测，
输出 12 维 joint-position action。动作顺序为：

```text
FL_hip, FL_thigh, FL_calf,
FR_hip, FR_thigh, FR_calf,
RL_hip, RL_thigh, RL_calf,
RR_hip, RR_thigh, RR_calf
```

替换低层模型时必须保持速度坐标系、单位、控制周期、关节顺序和动作持续时间
一致。

## 运行记录

默认运行结果保存在：

```text
NaVILA-Orca/outputs/scene_locomotion_smoke/
├── measurements.json
├── scene_alignment.json
└── frames/<run-id>/*.jpg
```

`measurements.json` 包含模型原始输出、动作块、目标与实际运动误差、采样周期、
相机配置、最终状态和终止原因。

测试时至少确认：

1. 每次模型请求包含 8 帧，最新帧位于最后；
2. `control_dt=0.02 s` 时，常规历史帧的 `step_id` 通常相差 25；
3. 每个动作块完成后只触发一次新推理；
4. policy、physics 和 OrcaLab 同步频率分别为 50、200 和 25 Hz；
5. `measurements.json`、场景对齐文件和图像目录完整生成。

## 代码位置

- 模型服务：[`navila_vlm_server.py`](../scripts/navila_vlm_server.py)
- 图像采样：[`frames.py`](../src/navila_orca/frames.py)
- 动作解析：[`actions.py`](../src/navila_orca/actions.py)
- 闭环时序：[`runner.py`](../src/navila_orca/runner.py)
- 默认参数：[`run_orcalab_scene_locomotion.sh`](../scripts/run_orcalab_scene_locomotion.sh)
