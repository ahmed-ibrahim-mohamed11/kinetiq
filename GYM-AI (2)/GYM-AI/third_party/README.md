# third_party/ — benchmark-only upstream repos (NOT committed; pinned here)

Used only by `ai/research/pose_benchmark/` (Phase 2.5C). The directory is git-ignored; a fresh
clone restores it with the commands below (SSL note: this machine needs `-c http.sslBackend=schannel`).

| Repo | Pinned commit | License | Used for |
|---|---|---|---|
| https://github.com/TaatiTeam/MotionAGFormer | `4756fd1eb7cc73f0e991f091ff2280e030ab85f3` | Apache-2.0 | model/MotionAGFormer.py (Base config) |
| https://github.com/QitaoZhao/PoseFormerV2 | `008036a66048e2dcb6fcf8d5b05a705ad1d2f392` | MIT | common/model_poseformer.py |
| https://github.com/facebookresearch/VideoPose3D | `1afb1ca0f1237776518469876342fc8669d3f6a9` | CC BY-NC 4.0 (research only) | common/model.py TemporalModel |

```powershell
git -c http.sslBackend=schannel clone https://github.com/TaatiTeam/MotionAGFormer third_party/MotionAGFormer
git -C third_party/MotionAGFormer checkout 4756fd1eb7cc73f0e991f091ff2280e030ab85f3
git -c http.sslBackend=schannel clone https://github.com/QitaoZhao/PoseFormerV2 third_party/PoseFormerV2
git -C third_party/PoseFormerV2 checkout 008036a66048e2dcb6fcf8d5b05a705ad1d2f392
git -c http.sslBackend=schannel clone https://github.com/facebookresearch/VideoPose3D third_party/VideoPose3D
git -C third_party/VideoPose3D checkout 1afb1ca0f1237776518469876342fc8669d3f6a9
```

Checkpoints are registered in `ai/models/README.md` (git-ignored binaries, re-download URLs there).
VideoPose3D's CC BY-NC license restricts it to non-commercial research use — fine for this benchmark,
NOT eligible for the product path without relicensing.
