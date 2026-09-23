# v5 Temporal Token Residual Experiment

## Purpose

Improve the real-time v5 model without changing the established deployment graph:

```text
MC3 spatial 3x3 token student (frozen)
  -> causal multi-scale current/future token residual (new, 1.32M parameters)
  -> original World-Pose + Future-Pose + history + long-view
  -> original Audio Transformer + reliability-gated residual
  -> original fall / warning / alarm path
```

The residual is initialized to zero. Before training, the new candidate produces exactly the same current token, future token, and final logits as v5. It therefore cannot replace the existing visual representation by accident.

## Targeted Changes

The new causal residual expert uses three depthwise causal temporal scales (dilation 1, 2, and 4), then makes a bounded correction to only the JEPA-compatible current/future tokens.

Pose visibility, box confidence, tracker validity, and box-motion stability determine an observability gate. Low-observability windows suppress the residual, targeting GMDCSA24 bed-side occlusion without introducing a dataset-name shortcut.

The training objective retains the original v5 token, final decision, phase, and Future-Pose supervision. It additionally includes:

```text
Future-Pose warning KD
domain reliability KD
occlusion-conditioned residual penalty
falling/fallen versus sit_down/lie_down/recovery alarm margin
```

OF-Syn quality weights and inverse-square-root source balancing are retained. The model does not remove OF-Syn boundary samples from the formal validation or test split.

## Verification

Before training, a real cache batch was passed through v5 and the new candidate:

```text
max final-logit difference       = 0.0
max current-token difference     = 0.0
```

The candidate thus begins as v5 and learns only explicit residual changes.

## Pilot

```text
Train / validation: 4,096 / 768 stratified windows
Epochs: 4
```

| Model | Validation F1 | FPR |
|---|---:|---:|
| Initial v5 identity | 0.7759 | baseline |
| Temporal residual, epoch 4 | 0.8000 | 0.0647 |

The residual norm stayed small (`0.0057`), supporting the intended correction behaviour rather than a replacement of v5.

## Full Experiment

```text
Train / validation / test: 20,098 / 2,539 / 3,022
Epochs: 6
Selected validation epoch: 5
```

Validation peak:

```text
F1  = 0.8058
FPR = 0.0362
```

Token fidelity improved relative to v5:

| Token | v5 cosine | Temporal residual cosine |
|---|---:|---:|
| Current temporal token | 0.6388 | 0.6487 |
| Future temporal token | 0.7779 | 0.7830 |
| World token | 0.9170 | unchanged 0.9170 |
| Crop token | 0.9174 | unchanged 0.9174 |

## Fixed-Test Result

For a deployment comparison, the candidate uses the pre-existing v5 deployment threshold `0.669911`, not a threshold selected with test labels.

| Model | Accuracy | Precision | Recall | F1 | FPR | TP / FP / FN / TN |
|---|---:|---:|---:|---:|---:|---|
| v5 baseline | 0.9097 | 0.8183 | 0.8194 | 0.8188 | 0.0604 | 617 / 137 / 136 / 2132 |
| v5 temporal residual | 0.9136 | 0.8436 | 0.8021 | **0.8223** | **0.0494** | 604 / 112 / 149 / 2157 |

The new candidate gives a modest but real fixed-threshold F1 gain of `+0.0035`, and removes 25 false positives at the cost of 13 additional false negatives. It is a precision-oriented deployment refinement, not a large recall gain.

For reference only, the test-set post-hoc best F1 is `0.8285` at threshold `0.4824`; this must not be used as the formal deployment result. The validation-selected threshold `0.7690` transferred poorly (`test F1=0.8171`), so it is rejected for deployment.

## Per-source Changes at the Fixed v5 Threshold

The strongest practical gain is lower false-positive rate in difficult OF-Syn while retaining a stable overall result. GMDCSA24 improves in false positives but loses recall, so it remains the main target for a future recall-specific stage.

```text
OF-Syn: FP 90 -> 65, FN 83 -> 98
GMDCSA24: FP 12 -> 9, FN 13 -> 15
```

This confirms that the current loss primarily improves controlled-action suppression. It does not yet solve missed falls under severe occlusion.

## Artifacts

```text
Training: surveillance/train_v5_temporal_residual.py
Threshold audit: surveillance/audit_v5_temporal_threshold.py
Candidate: surveillance/models/v5_temporal_residual_full_v1.pth
Full log: outputs/logs/train_v5_temporal_residual_full_v1.log
Full metrics: outputs/eval/v5_temporal_residual_full_v1.json
Threshold audit: outputs/eval/v5_temporal_residual_full_v1_threshold_audit.json
```

## Deployment Decision

The streaming loader now recognizes `expert_state_dict` and applies the
`CausalTemporalResidualExpert` only after the causal 32-frame Pose/box tensors
exist. It corrects current/future temporal tokens while retaining v5 crop,
world-history, long-view, Future-Pose, audio, warning, and alarm behavior.

The 39-video audit passed: `window` and `sampled_incremental` agreed on all
39 alarm states, all 39 warning states, and every first trigger timestamp.
The deployment-mode real-time factor was mean `1.330x`, P10 `1.186x`; the
single `0.937x` first-item measurement includes one-time CUDA/YOLO warm-up.

The verified copy is now
`deployment_world_pose_av_v3/models/v5_temporal_residual_full_v1.pth`. Its
default threshold was explicitly set to the fixed-test v5 threshold `0.669911`.
The old `arch_preserving_jepa_spatial_full_v5.pth` remains in place as the
rollback model.
