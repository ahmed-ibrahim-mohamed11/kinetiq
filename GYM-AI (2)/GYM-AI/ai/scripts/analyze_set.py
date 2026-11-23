#!/usr/bin/env python3
"""Mode A — after-set analysis in ONE command (Phase 5, P5.2).

  python -m ai.scripts.analyze_set VIDEO [--out result.json] [--device cpu|cuda] [--imgsz 640] [--fps F]

Writes a schema-validated AnalysisResult (ai/schemas/analysis_result.v1.json) and prints a short
summary. Exit codes: 0 = a valid result was produced (including an explicit abstention status
such as NO_PERSON — that is a correct answer, not a crash); 2 = the input could not be processed
(missing/undecodable video, invalid fps, missing model files), with the reason on stderr.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video")
    ap.add_argument("--out", default=None, help="result JSON path (default: <video>.analysis.json)")
    ap.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--artifact", default=None, help="rep-segmentation artifact (default: committed v1)")
    ap.add_argument("--fps", type=float, default=None,
                    help="override the container's frame rate (default: use the video's own metadata)")
    args = ap.parse_args(argv)
    if not os.path.isfile(args.video):
        print(f"ERROR: video not found: {args.video}", file=sys.stderr)
        return 2
    from ai.core.pipeline import analyze_video, load_components
    try:
        comp = load_components(device=args.device, artifact_path=args.artifact, imgsz=args.imgsz)
        result = analyze_video(args.video, comp, fps=args.fps)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    out = args.out or os.path.splitext(args.video)[0] + ".analysis.json"
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(result.model_dump_json(indent=1))
    s = result.summary
    print(f"status {result.status}: {s.n_reps} reps, {s.n_reps_with_attention} with coach-attention "
          f"indicators; mean rep {s.mean_rep_duration_s or 0:.2f} s; model {result.versions.artifact_version}"
          f"+{result.versions.artifact_fingerprint[:12]} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
