# Spatial v5 Audio-Visual Inference

This directory contains the standalone inference code and deployment instructions. Model checkpoints are not included in the public source snapshot. The original evaluation notes are retained under `docs/`.

## Run

Install dependencies from `requirements.txt`, obtain the required checkpoints from an authorized source, and place them under the model paths expected by `deployment_world_pose_av_v3/`. Then run `scripts/run_arch_jepa_spatial_streaming.py` with an input video and output path.

The documented evaluation reports Accuracy 0.9136, F1 0.8223, and FPR 0.0494 on a fixed seven-source test set; a 39-video audit reports mean real-time factor 1.330x. Results are limited to those protocols.

The V-JEPA2 base checkpoint is not bundled and must be obtained separately under its applicable terms. Do not commit videos, datasets, generated outputs, or model weights to the source tree.
