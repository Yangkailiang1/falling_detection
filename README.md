# Multimodal Fall Detection and Care System

Public source snapshot for a multi-part fall detection project. GitHub account: [Yangkailiang1](https://github.com/Yangkailiang1). The repository combines three deliverables:

- `spatial_v5_deployment/`: standalone audio-visual fall-inference package. Large model weights are intentionally not included; see its README.
- `analysis_platform/`: care platform with inference/backend services, monitoring web client, headless voice workflow, and family WeChat mini program.
- `algorithm_platform/`: anonymized event intake and analysis/iteration dashboard, with export tools for downstream experiments.

## System flow

Camera/audio input is processed by the care platform. Detected events are archived for family follow-up. When enabled, the integration sends anonymized event fields and optional skeleton sequences to the algorithm platform; it is designed not to upload raw video, screenshots, device serial numbers, or home locations. The mini program reads care events and device status through the care API.

## Local setup

1. For the care platform, copy `analysis_platform/.env.example` to `analysis_platform/.env`; this file configures the main Flask API (default port 5001) and the web client. Copy `analysis_platform/backend/.env.example` to `analysis_platform/backend/.env` only if you run the optional standalone mini API.
2. For the algorithm platform, copy `algorithm_platform/.env.example` to `algorithm_platform/.env`. Generate your own local secrets and use matching algorithm API key and source salt values on both platforms before enabling synchronization. Synchronization is disabled by default.
3. Follow each component README for Python and Node dependencies and local ports. Do not expose development servers to the public network. The mini program needs a configured care API URL and a valid development app ID.
4. Add the model checkpoints required by `spatial_v5_deployment/README.md` from their official or authorized sources.

The demo video is not included in this source release.

## Evaluation reference

The standalone inference README reports the fixed evaluation protocol and its metrics. These results apply to that specified test set and video audit, not to every deployment environment.

## Privacy and licensing

This source snapshot excludes credentials, local databases, event archives, recorded footage, device screenshots, local-only mini program settings, and model weight files. No blanket license is included; reuse rights have not been specified. Third-party components and weights retain their own terms.

## 演示视频

[下载演示视频（MP4）](https://github.com/Yangkailiang1/falling_detection/releases/download/v1.0.0/fall-detection-demo.mp4)
