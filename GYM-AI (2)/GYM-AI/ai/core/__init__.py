"""ai.core — the production rep-segmentation package (Phase 1).

Extracted verbatim from the frozen research baseline ai/research/rep_segmentation_v2_hsmm.py
(md5 60f99f7d..., see ai/research/README.md) and ai/research/extract_poses3d.py.
Behaviour contract: metrics identical to ai/outputs/baselines/v2_hsmm_nested_lovo.json
(enforced by ai/tests/test_regression.py). Do not change numerics here without a full
nested-LOVO rerun and a CHANGELOG entry.

Module map
  config       Cfg dataclass, hyper-parameter GRID, H36M joint indices, state constants
  pose         YOLO11n-pose 2D extraction + COCO-17 -> H36M-17 conversion
  lifting      MotionBERT (DSTformer) loading + 2D -> 3D lifting
  features     torso-frame features, smoothing, phase target, observation features
  phase_model  ridge phase model (shared L/R), arm fusion
  decoder      tempo estimate, explicit-duration HSMM decoder, reps + per-arm timing
  evaluation   IoU matching, per-video metrics, nested LOVO (select_config / run_cv)
  io           annotations / saved-model loading, segment_video() live entry point
"""

from ai.core.config import Cfg, GRID  # noqa: F401
from ai.core.io import segment_video  # noqa: F401
