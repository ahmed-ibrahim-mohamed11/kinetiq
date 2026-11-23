# Edge model files

This folder holds the ONNX weights the edge service loads at runtime. **No weights are committed**
(`*.onnx`, `*.engine` and `*.pt` here are git-ignored) and none is bundled with the Docker image:
each site downloads the files, verifies them and records their SHA-256.

Nothing listed here is *chosen*. These are the license-clean default **candidates** that the
adapters support out of the box; a model becomes the production choice only after it has been
benchmarked on authorised gym footage per [`docs/architecture/model-selection.md`](../../../docs/architecture/model-selection.md).

## Default candidates

| Stage | File (suggested name) | Source | License | Input | Adapter / setting |
| --- | --- | --- | --- | --- | --- |
| Person detector | `yolox_s.onnx` | [Megvii-BaseDetection/YOLOX releases](https://github.com/Megvii-BaseDetection/YOLOX/releases) (tag `0.1.1rc0`, asset `yolox_s.onnx`) | Apache-2.0 | 1x3x640x640 | `OnnxPersonDetector(layout="yolox")`, `EDGE_DETECTOR_LAYOUT=yolox` |
| Person detector (low-end GPU / CPU) | `yolox_nano.onnx` or `yolox_tiny.onnx` | same release | Apache-2.0 | 1x3x416x416 | same |
| Pose (top-down) | `rtmpose-m_body7_256x192.onnx` (the `end2end.onnx` inside the zip) | OpenMMLab RTMPose ONNX SDK zip on `download.openmmlab.com`, e.g. `https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip` (link listed in the [mmpose RTMPose README](https://github.com/open-mmlab/mmpose/tree/main/projects/rtmpose)) | Apache-2.0 | 1x3x256x192 | `RtmPoseOnnx`, `EDGE_POSE_KIND=rtmpose` (or `auto`) |

Notes:

- The YOLOX release ONNX files are exported **without** `--decode_in_inference`: the output
  `[1, N, 85]` is the raw head (x/y offsets, log w/h, sigmoid objectness, sigmoid class scores) and
  the adapter grid-decodes it with strides 8/16/32. Input is BGR 0..255, top-left padded with 114.
  A model re-exported *with* decode is not supported by `layout="yolox"` (the adapter refuses it with
  a clear error rather than producing wrong boxes).
- RTMPose body7 outputs `simcc_x [1,17,384]` and `simcc_y [1,17,512]` (SimCC split ratio 2.0) in
  COCO-17 keypoint order, which `edge_ai/pose/angles.py` consumes unchanged.
- The **code and released weights** are Apache-2.0. The weights were trained on public datasets
  (YOLOX: COCO; RTMPose body7: COCO, AI Challenger, CrowdPose, MPII, sub-JHMDB, Halpe, PoseTrack18)
  that carry their own terms; record the legal review of those terms in `model_versions.license`
  before production use.
- The AGPL-3.0 Ultralytics models (YOLOv8/11, YOLO-pose) still work with the default
  `layout="auto"` / `YoloPoseOnnx` adapters, but need a commercial license for this product.

## Download and record the SHA-256

Run from `services/edge-ai` (PowerShell). Download into a fresh folder, never run anything from it,
and only move the `.onnx` into `models/` once its hash is recorded.

```powershell
New-Item -ItemType Directory -Force .\models\_download | Out-Null
Invoke-WebRequest https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx -OutFile .\models\_download\yolox_s.onnx
Invoke-WebRequest <rtmpose-m body7 onnx_sdk zip URL from the RTMPose README> -OutFile .\models\_download\rtmpose-m.zip
Expand-Archive .\models\_download\rtmpose-m.zip .\models\_download\rtmpose-m   # contains end2end.onnx (+ pipeline/deploy json)
Move-Item .\models\_download\yolox_s.onnx .\models\yolox_s.onnx
Get-ChildItem .\models\_download\rtmpose-m -Recurse -Filter end2end.onnx | Select-Object -First 1 | Move-Item -Destination .\models\rtmpose-m_body7_256x192.onnx
# lowercase hex: model_versions.checksum_sha256 only accepts ^[0-9a-f]{64}$
Get-ChildItem .\models\*.onnx | ForEach-Object { "{0}  {1}" -f (Get-FileHash $_ -Algorithm SHA256).Hash.ToLower(), $_.Name }
Remove-Item -Recurse -Force .\models\_download
```

Record, for every file:

1. the source URL, release tag / archive name and download date;
2. the SHA-256 (lowercase) in the table below **and** in `model_versions.checksum_sha256`
   (component, name, version, runtime `onnxruntime`, license);
3. on every edge box, re-check the hash before the service is (re)started; a mismatch means the
   file is not the benchmarked one and must not be used.

| File | Source (URL, tag) | Downloaded | SHA-256 |
| --- | --- | --- | --- |
| `yolox_nano.onnx` | `Megvii-BaseDetection/YOLOX` (`0.1.1rc0`) | 2026-11-02 | `c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d` |
| `yolox_s.onnx` | `Megvii-BaseDetection/YOLOX` (`0.1.1rc0`) | 2026-11-02 | `c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063` |
| `rtmpose-m_simcc-body7_256x192.onnx` | OpenMMLab `rtmposev1/onnx_sdk` (`20230504`) | 2026-11-02 | `5c0a4bf67953e6d2ac43ce15e77dc9d5d354ae18430a47d2c5963a7bc5683e3c` |
| `face_detection_yunet_2023mar.onnx` | `opencv/opencv_zoo` (`main`, Apache-2.0) | 2026-11-08 | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` |
| `face_recognition_sface_2021dec.onnx` | `opencv/opencv_zoo` (`main`, Apache-2.0) | 2026-11-08 | `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79` |
| `person_reid_youtu_2021nov.onnx` | `opencv/opencv_zoo` (`main`, Apache-2.0) | 2026-11-08 | `0579683334d4b9440221606dcb461656dd0dc64143b18f48faedaced9b4f580d` |

## Smoke-test on a real image

```powershell
.\.venv\Scripts\python.exe -m edge_ai.tools.detect_image --detector .\models\yolox_s.onnx --layout yolox `
  --pose .\models\rtmpose-m_body7_256x192.onnx --pose-kind rtmpose `
  --image .\some-authorised-frame.jpg --out .\annotated.png --repeat 20
```

Prints JSON with the image size, the ONNX Runtime providers in use, per-stage latency
(mean / p50 / p95 over `--repeat` runs after one warm-up), the person boxes and, with `--pose`,
knee/elbow angles and the 17 keypoints per person. `--out` writes an annotated copy. Use only
images you are authorised to process; nothing is uploaded.

Multi-stream throughput: `python -m edge_ai.tools.benchmark --detector .\models\yolox_s.onnx --layout yolox --streams 4`.

## Runtime configuration

| Variable | Values | Default |
| --- | --- | --- |
| `EDGE_DETECTOR_MODEL_PATH` | path to the detector ONNX | unset = detection disabled |
| `EDGE_DETECTOR_LAYOUT` | `auto` (YOLO-style decoded exports), `yolox` | `auto` |
| `EDGE_POSE_MODEL_PATH` | path to the pose ONNX | unset = no rep counting |
| `EDGE_POSE_KIND` | `auto` (SimCC heads → RTMPose, else YOLO-pose), `rtmpose`, `yolo_pose` | `auto` |

`edge-ai run` builds its adapters through `edge_ai.detection.create_detector` and
`edge_ai.pose.factory.create_pose_estimator` (see `_build_models` in `edge_ai/__main__.py`) and
passes the layout / kind from `Settings` (`detector_layout`, `pose_kind`), so the values apply
whether they come from the process environment, `services/edge-ai/.env` or the Docker `env_file`.
`detect_image` and `benchmark` use the same factories; called without an explicit layout / kind,
the factories fall back to the process environment, then `auto`.

`auto` for the detector expects a YOLO-style export with decoded boxes. A raw YOLOX head such as
`yolox_s.onnx` needs `EDGE_DETECTOR_LAYOUT=yolox`. Under `auto` its grid offsets would be read as
boxes. `auto` for pose recognises RTMPose by its two SimCC outputs.
