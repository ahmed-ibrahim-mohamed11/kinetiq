#!/usr/bin/env python3
"""Fetch the PRODUCT checkpoints a fresh clone needs and verify them (sha256) against
docs/AI_CORE_BASELINE_MANIFEST.json.

  python -m ai.scripts.download_checkpoints           # download what is missing, verify everything
  python -m ai.scripts.download_checkpoints --check   # verify only, no network

Sources are the ones recorded in the manifest: the Ultralytics asset release (resolved by
ultralytics' own downloader) and the MotionBERT authors' Hugging Face repo. A file already on disk
with the wrong hash is reported and left alone (never silently overwritten); a download with the
wrong hash is discarded. Exit 0 only if every product checkpoint is present and verified.

Benchmark-only checkpoints (full MotionBERT, PoseFormerV2, MotionAGFormer, VideoPose3D, MediaPipe)
are not needed by the product, the tests or the headline metrics; their sources are listed in the
manifest and ai/models/README.md. Licences: see docs/LICENSING.md (YOLO11 is AGPL-3.0).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from ai.scripts.baseline_lib import MANIFEST_JSON, REPO_ROOT, sha256_file  # noqa: E402

HF_REPO = "walterzhu/MotionBERT"
SOURCES = {   # repo-relative path -> (downloader, remote name)
    "yolo11n-pose.pt": ("ultralytics", "yolo11n-pose.pt"),
    "ai/models/motionbert/FT_MB_lite_MB_ft_h36m_global_lite/best_epoch.bin":
        ("huggingface", "checkpoint/pose3d/FT_MB_lite_MB_ft_h36m_global_lite/best_epoch.bin"),
}


def expected() -> dict:
    with open(MANIFEST_JSON, encoding="utf-8") as f:
        models = json.load(f)["models"]
    exp = {m["path"]: m for m in models if m["path"] in SOURCES}
    missing = set(SOURCES) - set(exp)
    if missing:
        raise SystemExit(f"manifest has no entry for {sorted(missing)}")
    return exp


def _download(kind: str, name: str, tmp: str) -> str:
    if kind == "ultralytics":
        from ultralytics.utils.downloads import attempt_download_asset
        return attempt_download_asset(os.path.join(tmp, name))
    from huggingface_hub import hf_hub_download
    return hf_hub_download(repo_id=HF_REPO, filename=name, local_dir=tmp)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="verify only; never download")
    ap.add_argument("--root", default=REPO_ROOT, help="install root (default: this repository)")
    args = ap.parse_args(argv)
    bad = 0
    for rel, e in expected().items():
        dest = os.path.join(args.root, *rel.split("/"))
        if os.path.exists(dest):
            if sha256_file(dest) == e["sha256"]:
                print(f"OK        {rel}")
            else:
                print(f"MISMATCH  {rel}: sha256 differs from the manifest (left untouched)", file=sys.stderr)
                bad += 1
            continue
        if args.check:
            print(f"MISSING   {rel}", file=sys.stderr)
            bad += 1
            continue
        with tempfile.TemporaryDirectory(prefix="gym_ai_ckpt_") as tmp:
            try:
                got = _download(*SOURCES[rel], tmp)
            except Exception as ex:          # network/HTTP errors from either downloader
                print(f"FAILED    {rel}: download error: {ex}", file=sys.stderr)
                bad += 1
                continue
            if not got or not os.path.isfile(got):
                print(f"FAILED    {rel}: downloader returned no file", file=sys.stderr)
                bad += 1
                continue
            h = sha256_file(got)
            if h != e["sha256"]:
                print(f"FAILED    {rel}: downloaded sha256 {h[:16]}... != manifest {e['sha256'][:16]}... "
                      f"(discarded)", file=sys.stderr)
                bad += 1
                continue
            os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
            shutil.copyfile(got, dest)
        print(f"FETCHED   {rel} ({e['bytes']:,} B, sha256 verified; source: {e['source']})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
