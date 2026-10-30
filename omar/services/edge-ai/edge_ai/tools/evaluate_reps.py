"""
Rep-counting evaluation harness (Phase 6 acceptance tool).

Input: a CSV of labelled signal samples exported from authorised footage:

    t,value,confidence,label_rep_end
    0.00,166.1,0.91,0
    0.10,165.8,0.90,0
    ...
    2.45, 164.9,0.88,1      ← a human labeller marked a rep completing here

Usage:
    python -m edge_ai.tools.evaluate_reps --profile leg-press --csv data/legpress_cam2.csv [--tolerance 0.5]

Output: precision / recall / F1 of predicted rep completions vs labels within ±tolerance
seconds, plus the count error. These numbers — not promises — go into
docs/architecture/model-selection.md and PROGRESS.md.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from edge_ai.reps.profiles import RepProfile
from edge_ai.reps.state_machine import RepCounter
from edge_ai.tools.default_profiles import DEFAULT_PROFILES


@dataclass
class RepEvalResult:
    labelled: int
    predicted: int
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float
    f1: float
    count_error: int
    rejected_cycles: int


def evaluate(
    samples: list[tuple[float, float, float, int]], profile: RepProfile, tolerance_s: float = 0.5
) -> RepEvalResult:
    counter = RepCounter(profile=profile)
    predicted: list[float] = []
    labelled = [t for t, _, _, lab in samples if lab]
    for t, v, c, _ in samples:
        ev = counter.update(v, c, t)
        if ev:
            predicted.append(ev.completed_at)
    unmatched = list(labelled)
    tp = 0
    for p in predicted:
        match = next((lab for lab in unmatched if abs(lab - p) <= tolerance_s), None)
        if match is not None:
            unmatched.remove(match)
            tp += 1
    fp = len(predicted) - tp
    fn = len(labelled) - tp
    precision = tp / len(predicted) if predicted else (1.0 if not labelled else 0.0)
    recall = tp / len(labelled) if labelled else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return RepEvalResult(
        len(labelled),
        len(predicted),
        tp,
        fp,
        fn,
        round(precision, 4),
        round(recall, 4),
        round(f1, 4),
        len(predicted) - len(labelled),
        counter.rejected_cycles,
    )


def load_csv(path: Path) -> list[tuple[float, float, float, int]]:
    out = []
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out.append(
                (
                    float(row["t"]),
                    float(row["value"]),
                    float(row.get("confidence", 1.0)),
                    int(row.get("label_rep_end", 0)),
                )
            )
    out.sort(key=lambda r: r[0])
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--profile", required=True, help="exercise slug (default profiles) or path to a JSON rep_profile")
    p.add_argument("--csv", required=True, type=Path)
    p.add_argument("--tolerance", type=float, default=0.5)
    args = p.parse_args(argv)
    if Path(args.profile).exists():
        profile = RepProfile.from_catalog(json.loads(Path(args.profile).read_text(encoding="utf-8")))
    else:
        profile = RepProfile.from_catalog(DEFAULT_PROFILES[args.profile])
    result = evaluate(load_csv(args.csv), profile, args.tolerance)
    print(json.dumps(asdict(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
