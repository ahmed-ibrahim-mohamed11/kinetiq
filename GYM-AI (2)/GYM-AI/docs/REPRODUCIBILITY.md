# REPRODUCIBILITY.md — from a fresh clone to the headline numbers (Phase 5, P5.5)

Validated path: Windows 11, Python 3.10.11, CPU. The verification log at the end records an
actual run of these steps from a fresh clone. Linux via Docker: a Dockerfile is provided but has
not been built yet (Docker is not installed on the development machine).

## 0. What the repository does and does not contain

| item | in git? | how you get it |
|---|---|---|
| code, configs, the fitted rep-segmentation artifact, form thresholds, result JSON Schema | yes | clone |
| 2D detector caches (Mendeley 165 videos, seated-row 6, external 8) + all ground truth CSVs | yes | clone |
| product checkpoints (YOLO11n-pose, MotionBERT-Lite) | no | step 3 (downloaded, sha256-verified) |
| 3D pose caches the evaluation suites read | no | step 5 (rebuilt from the tracked 2D caches) |
| raw videos | no, never | private recordings / third-party content; not needed for the headline metrics (see "Limits") |

## 1. Clone and create a virtual environment

```powershell
git clone <repo-url> gym-ai
cd gym-ai
python -m venv .venv            # Python 3.10 (3.10.11 validated)
.venv\Scripts\activate
```

## 2. Install pinned dependencies

```powershell
# Windows, CPU (the numeric reference): PyPI's torch 2.14.0 for Windows is the CPU build
python -m pip install -r requirements/dev.txt

# NVIDIA GPU (validated: RTX 5050 Laptop, driver 610.74), or CPU on Linux (PyPI's Linux torch
# bundles CUDA): install torch + torchvision from the PyTorch index FIRST, WITHOUT dependencies,
# then the requirements, which supply every dependency from PyPI.
python -m pip install --no-deps --index-url https://download.pytorch.org/whl/cu130 torch==2.14.0 torchvision==0.29.0
python -m pip install -r requirements/dev.txt
```

Why `--no-deps`: the PyTorch index serves some of torch's dependencies only as source
archives that cannot be built from that index. Measured on 2026-10-10: `typing_extensions`
4.16.0 needs `flit_core`, which is absent there, so `pip install --index-url .../whl/cpu torch`
fails with pip 23.0.1. The pins in `requirements/core.txt` (`torch==2.14.0`) match any build of
that version (`+cpu`, `+cu130`).

`requirements/` is split: `core.txt` (inference + evaluation) ⊂ `service.txt` (+ FastAPI)
⊂ `dev.txt` (+ pytest) ⊂ `research.txt` (benchmark lifters, MediaPipe, plotting, ...).
The root `requirements.txt` installs everything.

Turn off Ultralytics usage analytics for your user account once (the product detector already
does this for its own process, but research scripts call Ultralytics directly; docs/PRIVACY.md):

```powershell
yolo settings sync=False
# equivalent if the yolo launcher is unavailable:
python -c "import sys; sys.argv=['yolo','settings','sync=False']; from ultralytics.cfg import entrypoint; entrypoint()"
```

## 3. Download the product checkpoints

```powershell
python -m ai.scripts.download_checkpoints          # --check verifies without downloading
```

This fetches `yolo11n-pose.pt` (6,255,593 B, Ultralytics release asset, **AGPL-3.0**: see
LICENSING.md) and MotionBERT-Lite `best_epoch.bin` (64,099,897 B, Hugging Face
`walterzhu/MotionBERT`). Both are verified against the sha256 in
`docs/AI_CORE_BASELINE_MANIFEST.json`. A wrong hash is never installed.

## 4. Run the tests

```powershell
python -m pytest -q
```

Tests that need git-ignored inputs (raw videos, the 3D caches before step 5) **skip with a
reason**; none may fail. The product regression tests (`test_product_regression.py`) need only
the tracked 2D caches plus the checkpoints, so they run here: known rep counts, metric ranges,
schema validity and determinism through the full product chain.

## 5. Rebuild the evaluation caches (CPU)

```powershell
python -m ai.scripts.rebuild_pose_caches
```

This writes the git-ignored MotionBERT-Lite 3D caches (Mendeley 165 videos, seated-row 6,
external 8) from the tracked 2D caches. It uses the production tracker and lifter, which are
proven equal to the research code that originally produced them. Use CPU: CUDA output differs
numerically and would not reproduce the frozen numbers bit for bit.

The rebuilt pose caches are not bit-identical to the originals; the confidence caches are.
Measured maximum pose differences: 5.4e-7 (seated-row), 2.1e-6 (external), 2.9e-6 (Mendeley).
Two causes: the tracked seated-row/external 2D files come from video-stream-mode detection
(within 1e-3 px of the per-frame detection behind the originals, `test_pose.py`), and torch
builds and machines differ at float32 rounding level. Step 6 checks that none of this moves a
reported number (verified: it does not). `--out-root DIR --compare` measures the differences on
any machine that has the original caches.

## 6. Reproduce the headline metrics

```powershell
python -m ai.scripts.verify_baseline --with-causal
```

This loads the committed artifact and checks its fingerprint and the product checkpoint hashes,
then recomputes every suite. Expected output, exit code 0:

| metric | value |
|---|---|
| system fingerprint | `63d36c31761c95a8…` |
| Mendeley VAL IoU F1 | 0.9137 |
| internal seated-row IoU F1 / pull F1 | 0.8406 / 0.8529 |
| external IoU F1 / pull F1 | 0.6316 / 0.8421 |
| causal V1 / V2 / V3 IoU F1 | 0.7397 / 0.8060 / 0.8125 |

`--source refit` re-fits the system on Mendeley TRAIN instead of loading the artifact. It must
give the same fingerprint.

## 7. Analyse a set (Mode A)

```powershell
python -m ai.scripts.analyze_set path\to\set.mp4 [--out result.json] [--device cuda]
```

Writes an `AnalysisResult` v1.0 (`ai/schemas/analysis_result.v1.json`). Exit 0 means a valid
result (including an explicit abstention such as `NO_PERSON`); exit 2 means an input error,
with the reason on stderr.

## 8. Local service

```powershell
python -m ai.service            # http://127.0.0.1:8000/docs ; contract: docs/AI_SERVICE.md
```

## 9. Docker (not yet verified)

```bash
docker build -t gym-ai .
docker run --rm -p 127.0.0.1:8000:8000 gym-ai
```

The image is CPU-only. It contains only the service's files (allowlist `.dockerignore`) and
downloads and verifies the checkpoints at build time. Ultralytics network calls are disabled at
runtime (`YOLO_OFFLINE=1`). Not built or run yet: Docker is not installed on the development
machine (owner action). After a first successful build, pin the base image by digest.

## Limits: what cannot be reproduced from the repository alone

- **Raw-video tests**: `test_pipeline.py`, the slow service test, and the detector-on-frames
  checks in `test_pose.py`/`test_live.py`. They need the private seated-row recordings and skip
  without them.
- **Re-running 2D detection** on Mendeley needs that public dataset's videos
  (DATASET_MENDELEY.md). The external stress-test clips are third-party content and are not
  redistributable (LICENSING.md). Neither is needed for the metrics: their 2D caches are tracked.
- **Timing**: Phase 4 speed numbers are machine-specific (LIVE_MODE.md).

## Verification log

**2026-10-10, fresh clone of commit `09fef10`, Windows 11, fresh venv (Python 3.10.11, pip
23.0.1), CPU, laptop on battery for most of the run.** Steps as written above, run by a script,
so no manual help.

| step | result | time |
|---|---|---|
| 1–2 clone + venv | ok | 27 s |
| 2 install, first attempt (`--index-url .../whl/cpu torch`) | **failed**: the PyTorch index offers `typing_extensions` 4.16.0 only as an sdist whose build backend it does not host. Instructions corrected to the `--no-deps` two-step (verified separately: torch 2.14.0+cpu, `pip check` clean) and to PyPI on Windows | 7 s |
| 2 install `requirements/dev.txt` (torch 2.14.0 CPU from PyPI) | ok, `pip check` clean | 440 s |
| 3 download checkpoints | both fetched, sha256 = manifest | 24 s |
| 4 tests, before the cache rebuild | **1 failed + 9 errors** in tests that assumed git-ignored caches. Fixed: shared skip markers in `ai/tests/conftest.py`, and tests that only need the fitted system now load the committed artifact. Re-run on a fresh clone: **591 passed / 27 skipped** with neither checkpoints nor caches, **603 passed / 15 skipped** with checkpoints and no caches; every skip names the command that provides the input | 63 s / 51 s / 245 s |
| 5 rebuild caches (179 videos) | ok; vs the originals: confidence bit-identical, pose max diff ≤ 2.9e-6 | 473 s |
| 6 `verify_baseline --with-causal` | **BASELINE VERIFIED**: fingerprint `63d36c31761c95a8` and all 8 headline metrics exact (0.9137 / 0.8406 / 0.8529 / 0.6316 / 0.8421 / 0.7397 / 0.8060 / 0.8125) | 93 s |
| 7 `analyze_set` on a dev video | status OK, 6 reps; JSON **identical** to this repository's result for the same video (timings excluded), across different torch builds | 16 s |

Not verified: the Docker build (Docker is not installed), and a non-Windows host.
