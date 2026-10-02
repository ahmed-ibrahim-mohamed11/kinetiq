# Vendored copy of MotionBERT

- Upstream: https://github.com/Walter0807/MotionBERT (Apache License 2.0, see `LICENSE`)
- Vendored 2026-10-02 at upstream commit `705d3a95354db8bdb696b3492e47a3b5537174ff` (shallow clone, `.git` removed).
- Used by `ai/research/extract_poses3d.py` for: `lib/model/DSTformer.py` (backbone) and
  `lib/utils/utils_data.py` (`crop_scale`, `flip_data`). Nothing in this folder is modified.
- The pose3d checkpoint is **not** committed (`*.bin` git-ignored) — see `ai/models/README.md` for the download source.
