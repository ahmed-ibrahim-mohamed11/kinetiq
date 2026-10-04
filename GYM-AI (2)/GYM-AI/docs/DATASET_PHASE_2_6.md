# DATASET_PHASE_2_6.md — expanded rep-segmentation dataset + FROZEN split (2026-10-05)

Artifacts (all committed):
- `ai/data/mendeley_rep_annotations_expanded.csv` — **165 videos, 2,472 reps, 24 subjects** (0-based
  frames, fps column; schema = Phase 2.5B + `annotation_quality`, `annotation_notes`)
- `ai/data/mendeley_subject_split.csv` — **FROZEN** train/val/test split (subject-level)
- `ai/data/mendeley_phase26_expansion_list.csv` — the 134-video expansion selection
- `ai/outputs/mendeley_phase26_dataset_report.json` — full statistics (source of the tables below)
- Tooling: `ai/research/mendeley_annotation/{phase26_build,phase26_finalize,make_subject_split}.py`
- The Phase 2.5B file `mendeley_rep_annotations.csv` is untouched (it is the 'verified' subset).

## Composition
| | total | train | val | test |
|---|---|---|---|---|
| subjects | 24 | 14 | 5 | 5 |
| videos | 165 | 94 | 34 | 37 |
| reps | 2,472 | 1,477 | 489 | 506 |
| back / curl | 2,109 / 363 | 1,252 / 225 | 448 / 41 | 409 / 97 |
| diagonal / front / side | 885 / 715 / 872 | 515 / 430 / 532 | 169 / 149 / 171 | 201 / 136 / 169 |
| bad / good form | 1,310 / 1,162 | 770 / 707 | 265 / 224 | 275 / 231 |
Rep duration: mean 27.6 / median 26 frames = 1.84 / 1.73 s (range 0.5–7.7 s); styles span ~1.2 s/rep
(subject_017/019) to ~3.6+ s/rep (subject_023/024). Side view is deliberately NOT under-sampled
(largest view bucket in train).

## Annotation quality tiers (per rep; nothing silently repaired)
| tier | reps | meaning |
|---|---|---|
| `verified` | 443 | Phase 2.5B: every video frame-verified by a human reviewer |
| `auto` | 1,412 | expansion drafts passing all QC rules; 12-video stratified sample (view x form) visually inspected, all structurally flagged videos reviewed at signal level |
| `uncertain` | 617 | failed a deterministic rep rule - kept as **DON'T-CARE** for evaluation: `weak` (peak < 0.45 x p90; a 24-rep visual sample showed a mix of genuine shallow reps and sway), `spike` (> 1.8 x p90; occlusion artifacts), `gap` (pull spacing < 0.5 x median; double-count suspects) |
**Evaluation rule for Phase 2.7+: a prediction matching an `uncertain` rep is neither TP nor FP;
an unmatched `uncertain` rep is not FN.** This avoids biasing the benchmark against shallow reps
while keeping unverifiable boundaries out of the score.
Exclusions: 3 duplicate file copies (annotated once), 4 short/truncated back videos (<8 s),
95 censored edge reps auto-dropped (logged in `drafts26/qc_summary.csv`), 1 manual drop
(`subject_017_back_good_front` P46 — passer-by occlusion, visually confirmed).

## FROZEN subject split (do not change after seeing model performance)
- Split unit = connected component of the MD5-duplicate subject graph:
  **{005,006}, {010,013}, {014,019,020}, {015,026}** (026 not in dataset) — all inside TRAIN.
- TRAIN (14): 001 003 005 006 007 010 012 013 014 016 018 019 020 023
- VAL (5): 002 008 015 017 022
- TEST (5): 004 009 011 021 024
- Assignment: deterministic greedy rep-balancing toward 60/20/20 with min 4 subjects in val/test;
  documented in `make_subject_split.py`. No subject, view or linked recording crosses splits.

## Readiness verdict for Phase 2.7
**Ready.** Every split contains all three views, both forms, both exercises, and ≥5 subjects;
side view is the largest or co-largest bucket everywhere; rep-duration spread covers ~3x tempo range.
Known limitations to carry into 2.7 reporting: (1) val contains no 'verified'-tier reps (quality
tiers are unevenly distributed because 2.5B subjects mostly landed in train/test) — use the
don't-care rule consistently; (2) 'auto' tier boundary precision is ~±4 frames (±0.27 s) vs ±3 for
'verified'; (3) all data is one public dataset at 15 fps — the final generalisation set (new
recordings, MASTER_PROMPT plan) remains required before any production claim.
