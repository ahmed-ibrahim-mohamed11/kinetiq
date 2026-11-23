"""
Tune a rep profile's hysteresis thresholds on labelled CSVs (Phase 6).

Grid-searches top_threshold / bottom_threshold (keeping timing fields from the base profile),
scores each candidate with `evaluate_reps.evaluate` over every CSV and picks the best mean F1;
ties go to the widest hysteresis band (most robust to pose jitter), then to the band centred in
the plateau of best candidates.

    python -m edge_ai.tools.tune_reps --profile biceps-curl --csv data/eval/biceps-curl_*.csv \
        [--write data/eval/biceps-curl.tuned.json]

Few clips from one person overfit easily: report the numbers as "N clips, 1 person, webcam",
never as general accuracy.
"""

from __future__ import annotations

import argparse
import glob
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from edge_ai.reps.profiles import RepProfile
from edge_ai.tools.default_profiles import DEFAULT_PROFILES
from edge_ai.tools.evaluate_reps import evaluate, load_csv


def _score(base: dict[str, Any], top: float, bottom: float, clips: list[Any], tol: float) -> tuple[float, list[Any]]:
    prof = RepProfile.from_catalog({**base, "top_threshold": top, "bottom_threshold": bottom})
    results = [evaluate(c, prof, tol) for c in clips]
    return sum(r.f1 for r in results) / len(results), results


def tune(base: dict[str, Any], clips: list[Any], tol: float, min_band: float = 25.0) -> dict[str, Any]:
    grid: list[tuple[float, float, float, list[Any]]] = []
    for top_i in range(100, 181, 5):
        for bottom_i in range(20, top_i - int(min_band) + 1, 5):
            f1_i, res_i = _score(base, float(top_i), float(bottom_i), clips, tol)
            grid.append((f1_i, float(top_i), float(bottom_i), res_i))
    best_f1 = max(g[0] for g in grid)
    plateau = [g for g in grid if g[0] >= best_f1 - 1e-9]
    widest = max(g[1] - g[2] for g in plateau)
    finalists = [g for g in plateau if g[1] - g[2] >= widest - 1e-9]
    finalists.sort(key=lambda g: (g[1], g[2]))
    f1, top, bottom, res = finalists[len(finalists) // 2]
    base_f1, base_res = _score(base, float(base["top_threshold"]), float(base["bottom_threshold"]), clips, tol)
    return {
        "baseline": {
            "top_threshold": base["top_threshold"],
            "bottom_threshold": base["bottom_threshold"],
            "mean_f1": round(base_f1, 4),
            "per_clip": [asdict(r) for r in base_res],
        },
        "tuned": {
            "top_threshold": top,
            "bottom_threshold": bottom,
            "mean_f1": round(f1, 4),
            "plateau_size": len(plateau),
            "per_clip": [asdict(r) for r in res],
        },
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--profile", required=True, choices=sorted(DEFAULT_PROFILES))
    p.add_argument("--csv", required=True, nargs="+", help="CSV files or globs")
    p.add_argument("--tolerance", type=float, default=0.75)
    p.add_argument("--write", type=Path, help="write the tuned rep_profile JSON here")
    args = p.parse_args(argv)
    paths = sorted({Path(f) for pattern in args.csv for f in (glob.glob(pattern) or [pattern])})
    clips = [load_csv(pth) for pth in paths]
    base = dict(DEFAULT_PROFILES[args.profile])
    out = {"profile": args.profile, "clips": [str(pth) for pth in paths], **tune(base, clips, args.tolerance)}
    print(json.dumps(out, indent=2))
    if args.write:
        tuned = {**base, "top_threshold": out["tuned"]["top_threshold"],
                 "bottom_threshold": out["tuned"]["bottom_threshold"]}
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(json.dumps(tuned, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
