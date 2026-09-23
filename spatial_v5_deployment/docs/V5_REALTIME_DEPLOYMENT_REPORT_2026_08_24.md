# v5 实时部署模型汇报

## 结论

当前部署模型为：

```text
surveillance/models/arch_preserving_jepa_spatial_full_v5.pth
```

该模型将原始大规模 V-JEPA 视觉编码器替换为 MC3 Spatial JEPA-compatible student，同时保留 Pose、Future-Pose、World-Pose 融合、历史上下文、长视角和音频残差结构。最新部署改造只优化了流式 Pose 缓存与摄像头输入队列，没有重新训练或修改模型权重、阈值、融合结构和报警策略。

固定测试协议下的正式 F1 为 **0.8188**；39 条本地视频复测证明改造前后全部 alarm/warning 状态和首次触发时间一致。实时吞吐从不足实时提升为稳定高于实时，最低实测 `1.185x`。

## 1. 正式检测指标

测试集为固定 OmniFall-derived causal split，`test=3022` 个样本。阈值只在 validation 集选择：

```text
decision threshold = 0.6699
```

| 指标 | 值 |
|---|---:|
| Accuracy | 0.9097 |
| Precision | 0.8183 |
| Recall | 0.8194 |
| F1 | **0.8188** |
| FPR | 0.0604 |
| TP / FP / FN / TN | 617 / 137 / 136 / 2132 |

补充说明：若在测试集上重新选择最佳阈值，F1 可为 `0.8217`，但这属于 test-best，不能作为正式主指标。汇报时应使用 `F1=0.8188`。

教师模型的离线 F1 为 `0.8745`，但教师采用大 V-JEPA，不能满足实时部署；v5 是当前可实时运行的学生模型。

### 分数据集结果

| 数据集 | Accuracy | Precision | Recall | F1 | FPR |
|---|---:|---:|---:|---:|---:|
| GMDCSA24 | 0.7312 | 0.6364 | 0.6176 | 0.6269 | 0.2034 |
| CAUCAFall | 0.9574 | 0.9444 | 0.9444 | 0.9444 | 0.0345 |
| EDF | 0.8906 | 0.8056 | 0.8056 | 0.8056 | 0.0761 |
| LE2I | 0.9409 | 0.8974 | 0.8140 | 0.8537 | 0.0250 |
| OCCU | 0.9208 | 0.8000 | 1.0000 | 0.8889 | 0.1159 |
| OF-Syn | 0.9103 | 0.7561 | 0.7707 | 0.7633 | 0.0575 |
| UP-Fall | 0.9253 | 0.9315 | 0.8947 | 0.9128 | 0.0510 |

当前主要难点仍是跨被试、床边遮挡的 GMDCSA24，以及包含主动躺下和恢复动作的 OF-Syn。这两个来源拉低了总体 F1，因此不能只看高分的 CAUCAFall/UP-Fall。

## 2. 部署后实时数据

硬件：RTX 4060 Ti；检测更新频率：4 Hz；测试目录：`D:/pose/测试视频`，共 39 条视频。

| 指标 | 旧 window Pose | 新 sampled_incremental Pose |
|---|---:|---:|
| 稳定平均实时因子 | 0.971x | **1.367x** |
| 稳定中位实时因子 | 0.913x | **1.227x** |
| 稳定 P10 实时因子 | 0.892x | **1.201x** |
| 最慢视频实时因子 | 0.738x | **1.185x** |
| 平均检测窗口耗时 | 217.6 ms | **140.6 ms** |

`1.185x` 表示最慢实测视频每播放 1 秒，系统仍可完成约 1.185 秒的视频推理，有约 18.5% 的吞吐余量。真实摄像头/RTSP 入口采用单帧 latest-frame queue：若出现短暂负载波动，系统丢弃陈旧帧，只处理最新帧，避免延迟排队累积。运行时可通过 timing JSON 的 `dropped_stale_frames` 监控这一保护机制。

### 流式一致性

实时改造后对相同 39 条输入复测：

```text
alarm 状态一致：               39 / 39
warning 状态一致：             39 / 39
首次 alarm 时刻一致：          21 / 21 个触发 alarm 的视频
首次 warning 时刻一致：        全部触发 warning 的视频
```

因此，速度提升来自删除重叠窗口中重复的 YOLO Pose 计算，不是通过降低检测频率、提高阈值或改变报警行为取得的。

### 个人视频验证

已有人工确认标签的 7 条独立个人跌倒视频 `1,2,4,5,6,7,8.mp4` 全部触发 confirmed alarm：

```text
confirmed-fall recall = 7 / 7 = 1.000
```

这 7 条均为正样本，其他哈希命名视频尚无真值标签，因此不能以 39 条个人视频计算 Accuracy、Precision 或 F1。它们用于验证跨相机场景的报警响应和速度，不替代固定测试集指标。

## 3. Future-Pose 预警指标

Future-Pose 分支只输出 `warning`，确认跌倒仍由完整 World-Pose-AV 融合头输出 `alarm`。

| Future-Pose warning 独立测试指标 | 值 |
|---|---:|
| Accuracy | 0.9269 |
| Precision | 0.7187 |
| Recall | 0.7488 |
| F1 | 0.7334 |
| FPR | 0.0455 |
| warning threshold | 0.7160 |

未来姿态预测本身的离线质量：归一化 Pose 误差 `0.0781`，可见性准确率 `0.9353`，未来 bbox MAE `0.0404`，world latent cosine `0.8747`。

在本地 39 条视频中，21 条同时触发 warning 与 alarm 的视频里，11 条 warning 早于 alarm：典型提前 `0.267 s`，最大 `4.670 s`。这只能说明预警先于模型确认；由于多数个人视频没有逐帧 impact 真值，不能把它直接写成“提前真实跌倒多少秒”。

## 4. 模型框架

```text
16-frame Full RGB ──┐
                     ├─> Kinetics MC3 backbone -> 3x3 spatial features
16-frame Person Crop ┘                              -> Spatial pooling
                                                    -> 6-layer causal Transformer student
                                                    -> world/current/future/crop tokens

32-frame YOLO Pose + bbox
  -> Pose Future Predictor -> predicted 16-frame future pose
  -> World-Pose cross-attention + pose expert fusion
  -> history tokens (3) + long-view tokens (6 s)
  -> p_visual / p_candidate

3-second log-mel audio -> Audio Transformer -> AV attention
  -> utility/reliability gate + bounded audio residual

p_visual + AV residual -> p_final -> temporal smoothing -> confirmed alarm
Future-Pose head -------------------------------------> warning
```

处理原则：视觉与 Pose 是主要跌倒证据；音频只作为受 reliability gate 限制的残差增益。音频缺失或不可靠时，模型不会伪造音频输入，而是退回视觉/Pose 主路径。

## 5. 关键结构参数与规模

| 模块 | 设置 |
|---|---|
| RGB 输入 | Full frame + person crop；16 帧；运行时先对齐 384，MC3 内部使用 112x112 |
| MC3 backbone | Kinetics-400 预训练，layer1--layer4 |
| 空间 token | 每帧 3x3x512，保留人体与局部场景位置 |
| Spatial student | dim=512，6 层 causal Transformer，8 heads，dropout=0.10 |
| Pose 输入 | 32 帧 x 17 joints x `(x,y,confidence)`，另含 bbox |
| Future-Pose | 预测未来 16 帧，约 1.0 s 预测视野 |
| 下游融合 | dim=256，8 heads，Pose layers=2，Pose predictor layers=2，fusion layers=2 |
| 长上下文 | history size=3；long view=6 s，每 6 s 更新 |
| 音频 | 22.05 kHz、3 s log-mel；缺失音频显式标记为 invalid |
| 模型阈值 | final=0.6699；warning=0.7160 |
| 报警策略 | 4 Hz 更新；2 个连续窗口确认；高置信 fast-confirm=0.93 且需 Pose 证据 |
| 冷启动保护 | 先累积完整 32 帧因果上下文，约 1.94 s 内不允许报警 |

参数量：

| 部分 | 参数量 |
|---|---:|
| MC3 backbone | 11.49 M |
| Spatial JEPA-compatible student | 32.40 M |
| World-Pose-AV fusion 与音频分支 | 18.14 M |
| v5 推理主模型合计 | **62.02 M** |
| YOLO11n-Pose | 2.87 M |
| 部署总参数量 | **64.90 M** |

权重文件：v5 主权重 `197.3 MiB`，YOLO11n-Pose `6.0 MiB`，Future-Pose warning checkpoint `62.7 MiB`。

## 6. 训练设置

训练使用 OmniFall-derived causal cache，并对齐 Full RGB、Crop RGB、Pose、Future-Pose、历史/长视角 token 和音频缓存。

| 项目 | 设置 |
|---|---|
| 训练 / 验证 / 测试样本 | 20,098 / 2,539 / 3,022 |
| 覆盖来源 | GMDCSA24、CAUCAFall、EDF、LE2I、OCCU、OF-Syn、UP-Fall |
| 训练轮数 | 8；其中第 1 轮为 latent warm-up |
| batch size | 8 |
| 随机种子 | 67 |
| student learning rate | 2e-5 |
| adapter learning rate | 2e-6 |
| token / label / final KD / proximal 权重 | 1.05 / 0.90 / 0.80 / 0.06 |
| 采样策略 | 按 `(source, label, phase)` 组大小的逆平方根加权 |

训练时冻结成熟的 World-Pose-AV 下游语义，只适配视觉学生与接口模块，使学生输出与原始 JEPA token contract 对齐。测试 token cosine：world=`0.9170`、crop=`0.9174`、current=`0.6388`、future=`0.7779`、pose-future=`0.9636`。

## 7. 当前可汇报表述

```text
我们在保持 World-Pose、Future-Pose、跨模态音频残差融合和报警策略不变的前提下，使用 Spatial JEPA-compatible student 替换大 V-JEPA 编码器。

固定测试协议下，模型取得 Accuracy=0.9097、Precision=0.8183、Recall=0.8194、F1=0.8188、FPR=0.0604。

通过将 YOLO Pose 改为 16 Hz 增量缓存，4 Hz 的 32 帧检测窗口直接复用已有骨架，39 条真实视频的报警结果与触发时刻完全保持一致；稳定平均实时因子提升至 1.367x，最慢视频仍为 1.185x。摄像头入口进一步使用只保留最新帧的有界队列，因此在短时算力波动下不会累计报警延迟。
```

## 8. 模型框架文字介绍

原始教师模型以一段连续视频为输入。它一方面使用全帧 RGB 保留人物与环境的整体关系，另一方面根据人体检测框得到人物 Crop，从而减少背景对跌倒判断的干扰。大规模 V-JEPA 从这两路视频中提取世界表征，并给出当前时刻、未来时刻和人物区域对应的 token。与此同时，YOLO Pose 提取 32 帧、17 个关键点及人体框轨迹。Pose Future Predictor 以当前骨架序列和世界 token 为条件，预测未来 16 帧的骨架坐标、可见性与人体框变化。世界 token 与观测 Pose、预测 Pose 通过 World-Pose Cross-Attention 融合，再结合三个历史 token 和六秒长视角 token，形成以视觉和运动为主的候选跌倒表示。

音频支路将最近三秒音频转换为 log-mel 特征，并由 Audio Transformer 编码。音频不会直接覆盖视觉判断，而是通过 utility/reliability gate 控制其残差增益：音频可靠时，音视频交互可以补充撞击声、呼救声或运动声；音频缺失、静音或不可信时，残差受限，模型主要依据 World-Pose 表示判断。最终概率经过时间平滑，并要求连续两个检测窗口超过阈值才发出 confirmed alarm。Future-Pose head 独立输出 warning，因此系统可以先提示潜在风险，再由完整融合头确认跌倒。

蒸馏后的 v5 部署模型没有改变上述 Pose、Future-Pose、World-Pose、历史/长视角和音频残差的下游语义。改变的仅是最前端的大 V-JEPA 编码器：它被 Kinetics-400 预训练的 MC3 backbone 和 Spatial JEPA-compatible student 替换。学生模型仍同时读取全帧与人物 Crop 的 16 帧 RGB；MC3 为每帧保留 3x3 的局部空间特征，Spatial Attention Pooling 将其汇聚为带有人体位置和局部场景信息的 token，六层因果 Transformer 再建模动作随时间的演化。学生最终输出与教师完全相同接口的 world、current、future 与 crop token，因此可以原样接回冻结的 World-Pose-AV 下游网络。这样既保留了世界模型的未来表征和多模态融合能力，又把大 V-JEPA 的运行代价替换为约 62M 参数的部署主模型。

## 9. 蒸馏策略

这里的蒸馏应准确称为“跨架构 JEPA 表征蒸馏（cross-architecture representation distillation）”，而不是把 V-JEPA 的每一层权重直接裁剪、量化或复制给更小 ViT 的同构权重压缩。教师是大 V-JEPA，学生是 MC3 + Spatial Transformer；两者网络结构不同，学生不继承教师的 Transformer 权重。标准知识蒸馏并不要求师生同构，除了最早的 logit distillation，也包括特征蒸馏、注意力蒸馏、关系蒸馏和表征蒸馏。本工作属于其中的 token-level feature/representation distillation，并额外加入最终决策行为蒸馏。

蒸馏不是只让学生模仿最终的跌倒概率，而是首先让学生复现教师在不同时间语义上的 token 表示。训练样本由对齐的 Full RGB、Person Crop、Pose、教师 world token、教师 current/future token、Crop token、历史/长视角 token 与音频缓存组成。第一个 epoch 只进行 latent warm-up，使新的空间 token 模块先学会稳定恢复教师表征；后续七个 epoch 再联合加入类别监督和最终决策蒸馏。

损失由四部分组成：token recovery 权重为 `1.05`，标签监督权重为 `0.90`，教师最终行为蒸馏权重为 `0.80`，保持与初始部署接口接近的 proximal 项权重为 `0.06`。训练按 `(source, label, phase)` 分组做逆平方根加权采样，避免 OF-Syn 等大来源完全主导优化，并保留 falling、fallen、lie_down、recovery 等难阶段。World-Pose、Future-Pose、历史/长视角和音频融合模块保持原有拓扑，学生只适配视觉 token 生成与接口矩阵；日志显示可训练的适配参数约 `4.23M`，学生总参数为 `32.40M`。

这种策略的重点是保持系统行为：学生不是重新学习一个独立的轻量跌倒分类器，而是学习替代教师视觉表征，再由原有的 Pose 与音视频决策链完成判断。固定测试上 world/crop token cosine 分别为 `0.9170/0.9174`，预测 Pose 接口 cosine 为 `0.9636`，说明学生与教师的关键接口已较好对齐。

它的“压缩”体现在部署代价而不是逐层权重变换：大 V-JEPA 编码器被移除，运行时视觉部分改为 MC3 backbone（11.49M）和学生（32.40M），并与原有 18.14M 下游融合模块共同部署。要使该说法更严格，汇报中应同时给出三类证据：token cosine、固定阈值下游 F1，以及端到端实时吞吐；本项目已给出这三类数据。若未来目标是“同构 V-JEPA 压缩”，则需要构建缩小深度/宽度的 ViT 学生并对齐 patch token 和每层表示，那会是另一条技术路线。

## 10. 去除 GMDCSA24 的六源子集汇总

下面的结果**没有修改模型、训练数据、阈值或任何预测**；仅从固定测试集总混淆矩阵中减去 GMDCSA24 的 `TP/FP/FN/TN=21/12/13/47`，用于说明其余六个来源上的表现。

| 测试范围 | 样本数 | Accuracy | Precision | Recall | F1 | FPR | TP / FP / FN / TN |
|---|---:|---:|---:|---:|---:|---:|---|
| 七源完整测试集 | 3022 | 0.9097 | 0.8183 | 0.8194 | 0.8188 | 0.0604 | 617 / 137 / 136 / 2132 |
| 六源子集，排除 GMDCSA24 | 2929 | **0.9153** | **0.8266** | **0.8289** | **0.8278** | **0.0566** | 596 / 125 / 123 / 2085 |

GMDCSA24 是跨被试、床边遮挡和主动躺下样本占比高的困难域，单源 F1 为 `0.6269`。排除该域后，整体 F1 提升 `0.0090`。汇报时可以将 `0.8278` 描述为“在不含 GMDCSA24 的六个来源上的同阈值子集汇总”；正式完整实验仍应保留七源主结果 `F1=0.8188`，以避免后验挑选数据集造成不公平比较。
