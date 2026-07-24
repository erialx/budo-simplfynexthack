<p align="right"><sub><a href="VLN_FINE_TUNING.md">English</a> · <strong>中文</strong></sub></p>

# 高层 VLN 适配：SFT 与 LoRA

该赛道修改决策模型，而不是行走控制器。收集和评估高层导航数据时，应固定 Go2 运动 checkpoint。稳定接口是[架构](ARCHITECTURE_zh.md)中定义的规范动作词表。

## 先建立基线，再考虑训练

先运行默认仓库案例，保存结果目录、检查 RGB 帧并审核动作序列。基线复现让每个团队在改变模型之前拥有一致的相机约定、场景布局、命令词表和输出格式。

```bash
./scripts/run_orcalab_scene_locomotion.sh \
  --output outputs/warehouse_baseline
```

## 收集可审核样本

开发包提供高层记录的小型导出器：

```bash
python scripts/export_vln_sft_records.py \
  outputs/warehouse_baseline \
  --output data/vln_review_queue.jsonl
```

它会生成与模型无关的记录，包含指令、保存的图像路径、基线动作、场景元数据和 `unreviewed` 标记。这是审核队列，**不是 ground truth**。训练前请检查图像/动作对齐，并附加人工标注：

```bash
python scripts/export_vln_sft_records.py \
  outputs/warehouse_baseline \
  --output data/vln_reviewed.jsonl \
  --label "turn left 15 degrees"
```

每条记录形如：

```json
{
  "instruction": "Move to the blue barrel and stop.",
  "image_paths": ["/abs/path/000_step_000000.jpg"],
  "baseline_actions": ["move forward 25 cm", "stop"],
  "target_action": "turn left 15 degrees",
  "review_status": "reviewed"
}
```

请将其适配到 NaVILA 训练发行版要求的确切数据集模板；不要假定上述 JSONL 可以直接交给任意 trainer。

## SFT 方向

当改进主要与语义或任务特定行为有关时，使用 SFT：

- 仓库术语：托盘、锥桶、出口标志、巡检点；
- 目标或风险附近更清晰的停止策略；
- “抵达货架 A，再检查通道”这样的分阶段路径；
- 仍位于受支持命令词表内的动作表达。

实用的第一版数据集应当小而精：采集固定场景状态，为每个状态编写一条无歧义指令，审核期望下一动作，并保留不同相机位置和物体摆放用于评测。assistant target 应只保留一个动作；运行时已会将其转换为固定运动片段。

## LoRA 方向

不需要完整 NaVILA 微调时使用 LoRA。在 NaVILA 训练环境中：

1. 从主办方提供的 NaVILA checkpoint 开始；
2. 冻结基础模型，在已审核导航记录上训练 adapter；
3. 保持图像历史长度与 prompt 格式与部署一致；
4. 通过 `NAVILA_SERVER_SCRIPT` 使用的 NaVILA server 合并或加载 adapter；
5. 在改变场景资源前，重新运行相同的固定 Orca_VLN 回合。

具体 target module、图像处理器和启动命令由主办方采用的 NaVILA 发行版决定；竞赛基线有意不将它们写死。

## 评测清单

报告不应只说明机器人最终是否移动：

- 有效动作率：输出恰好解析为一个允许动作；
- 指令遵循：对已审核状态作出正确的转向/前进/停止决定；
- 视觉落地：目标物或相机视角改变时，行为相应改变；
- 闭环结果：到目标的最终距离、路径轨迹和保存的 RGB 证据；
- 回归：适配后模型仍可运行基线仓库回合。

若模型提出无效动作，应修正高层数据、prompt 或模型输出，而不是修改 Go2 策略。
