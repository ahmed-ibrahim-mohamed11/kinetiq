# PHASE6_ANNOTATION_GUIDE.md — marking reps in the Phase 6 recordings

This guide is for the two people who mark the Phase 6 videos: the primary annotator **`a1`** (the
owner), who marks every take, and the second annotator **`a2`**, who independently marks a small
subset so that the markings can be checked. Use only these pseudonymous ids, never a name.

These annotations are the **ground truth of the final test**. They decide whether the system passes,
so they must be careful, consistent and **independent of the model**. The rules below were fixed
before any recording existed (docs/PHASE6_PREREGISTRATION.md §3) and are frozen at tag
`phase6-tools-v1`. Later clarifications go only into `docs/PHASE6_ADDENDA.md` as dated entries,
with examples; they never change a definition or a confidence tier.

## 1. Ground rules
1. **Blind.**
   - Never look at the system's output for these videos: no analysis, no service, no GYM-AI app,
     no live mode on them, until the evaluation has run.
   - The annotation tool shows only the video and your own marks; it loads no model.
   - `a2` works in a separate hand-off folder that contains only the subset videos, never `a1`'s
     marks, and the two annotators do not discuss the takes before the agreement is measured.
2. **No drafts.** Every mark is placed by a person, frame by frame. No automatic proposals.
3. **Only takes that passed the intake check.** Takes that FAILED the intake check are never
   annotated; the tool does not offer them.
4. **Don't touch the videos.** No re-encoding, trimming or renaming. Every annotation records the
   file's sha256, frame count, frame rate and decoder version. If any of these changes, the tool
   refuses to open the take with your old marks; `--restart` archives the old marks and starts
   afresh. Marks are never moved silently onto a different file.
5. **Frozen once locked.** After the data lock (pre-registration §3.6), annotations are never edited.
   A mistake found later is reported, not fixed.
6. **Everything stays on the owner's laptop**, in the data root outside OneDrive
   (`%LOCALAPPDATA%\gym-ai\phase6` by default). The tools refuse a cloud-synced folder.

## 2. The three marks (the same definitions as all development data)

| mark | what to look for | rule for holds |
|---|---|---|
| **START** | the first frame the handle starts moving toward the body from full extension (elbows begin to bend, hands begin to move back) | after an extension hold, START is the frame the pull begins (the end of the hold) |
| **PULL** | peak contraction: handle closest to the torso, elbows furthest back | squeeze hold: the **middle frame** of the hold |
| **END** | the first frame the arms are fully extended again after the return | extension hold: END is the **first** frame of the hold; the hold belongs to the rest before the next START |

How to read the marks in practice:
- Order is always START < PULL < END, and reps never overlap. The tool refuses anything else.
- **Slow sets**: the same definitions, but the movement starts and ends gradually. Step frame by
  frame near START and END, and pick the first frame of visible motion and the first frame of full
  extension.
- **Paused sets** (a squeeze hold AND an extension hold in every rep): apply both hold rules. A rep
  with holds is still one rep: START at the end of the extension hold, PULL in the middle of the
  squeeze hold, END at the first frame of the next extension hold.
- **Deviated sets** (asymmetric pull, trunk swing, short range, one side late, uneven tempo): mark
  boundaries exactly as for good reps; form never changes the definitions. A short-range rep is
  still a rep if it has a pull and a return.

Target precision: within 2 frames for START and END, 1–2 frames for PULL. PULL timing is the most
sensitive number in the evaluation.

## 3. Which reps to mark, and the confidence label
- Mark **every rep that starts and ends inside the video**.
- A rep cut off by the start or end of the recording is **not** marked. The evaluation ignores
  detections outside the annotated span.
- Confidence: the tool saves new reps as `high`. Press **C** on a rep to cycle it to `medium` or
  `low`.

| label | when to use it |
|---|---|
| `high` | all three marks are clear |
| `medium` | it is clearly a rep, but one mark is uncertain by a few frames (motion blur, the hand partly hidden) |
| `low` | you are not sure it is a complete rep: an aborted or partial pull, a double pump, a movement mostly hidden, a warm-up twitch |

`low` reps are "don't-care": the system is neither rewarded nor punished for them. Never force an
ambiguous movement into `high`, and never skip a genuinely ambiguous movement because it is hard;
mark it `low`. The tier describes the movement, not how hard the view or tempo is.

Limits checked before the evaluation (pre-registration §2.2): every take needs **≥ 3 certain reps**
(`high` or `medium`), and `low` reps may be at most **10%** of all reps and **20%** within any view or
tempo. If a limit is exceeded, the affected takes are **re-recorded**; `low` labels are never changed
to meet a limit.

## 4. The tool
```
python -m ai.scripts.annotate_reps --annotator a1                  # opens your next unfinished take
python -m ai.scripts.annotate_reps --annotator a1 --status         # done / in progress / missing
python -m ai.scripts.annotate_reps --annotator a1 --video A03_side_paused_take1
python -m ai.scripts.annotate_reps --annotator a1 --video A03_side_paused_take1 --restart
```

| keys | action |
|---|---|
| **SPACE** | play / pause |
| **A / D** (or ← / →) | one frame back / forward |
| **S / W** (or ↓ / ↑) | 10 frames back / forward |
| **J / L** | one second back / forward |
| **M** | mark the next boundary of the current rep (START → PULL → END); END saves the rep |
| **U** | undo (also removes an open START / PULL left from an earlier session) |
| **X** | delete the rep under the cursor |
| **C** | cycle confidence |
| **N / P** | next / previous rep |
| **F** | mark the take finished |
| **H** | help |
| **Q / ESC** | save and quit |

The bar at the bottom shows your reps: green = high, yellow = medium, red = low; white ticks = PULL.

Every change is saved immediately to `annotations/<annotator>/<video>.csv` with a `.meta.json`
sidecar. Edit marks only in the tool: a CSV edited by hand is detected and refused.

Suggested routine per take:
1. Play it once at normal speed and count the reps.
2. For each rep: stop just before it, step to START, press M; step to PULL, press M; step to END,
   press M.
3. Check that the rep count matches your first count.
4. Press F.

Expected effort: about 150 takes of 5–12 reps for `a1` (roughly 10–15 hours, best spread over
several days); `a2` marks about 15–16 takes (about 2 hours) per agreement round.

## 5. Agreement check (before the model is run; pre-registration §3.4)
1. `a1` finishes **every** take first. Then:
   `python -m ai.scripts.annotation_agreement --select-subset --second a2`
   draws the round's subset by a fixed rule (≥ 8 takes and ≥ 10% of all takes, every view and
   tempo, seeded; nobody picks takes by hand).
2. `python -m ai.scripts.annotation_agreement --handoff <folder>` copies only those videos, the
   manifest and the round record into a separate local folder (for example
   `%LOCALAPPDATA%\gym-ai\phase6_a2`). `a2` works there, on the same laptop:
   `python -m ai.scripts.annotate_reps --annotator a2 --root <folder>`.
3. `python -m ai.scripts.annotation_agreement --collect <folder>` brings `a2`'s marks back.
4. `python -m ai.scripts.annotation_agreement --measure` scores `a2` against `a1`. Accepted
   (pre-registered) when **IoU F1 ≥ 0.90 and pull MAE ≤ 0.15 s**, decided exactly. The result is
   written once and never overwritten; `qa/annotation_agreement_round<r>.md` lists every
   disagreement.
5. **Not accepted:**
   - write a dated addendum in `docs/PHASE6_ADDENDA.md` (`## A6-NN — date — title`) with
     clarifying examples drawn from the listed disagreements; it may not change a definition or a
     tier;
   - `a1` re-reviews **every** take with the clarification;
   - draw a new round: `--select-subset --addendum A6-NN`. Its subset never repeats an earlier
     round's takes. Repeat steps 2–4.
   No individual rep is adjudicated: the ground truth stays `a1`'s files. Every round, failed ones
   included, appears in the final report, with how many of `a1`'s files changed after each round.
6. `python -m ai.scripts.annotation_agreement --note "<what happened>"` adds a note to the round
   (never a name). `--status` lists the rounds.

The final run recomputes every round from the files and refuses to run unless the latest round is
accepted.

## 6. Finalize
```
python -m ai.scripts.annotate_reps --annotator a1 --finalize
```
This builds `annotations/reps.csv` from `a1`'s finished files. It refuses if a take is not finished,
a rep is left open, a file changed after annotation, a CSV was edited outside the tool, or the frame
count, frame rate or decoder differs from the intake check. Then run
`python -m ai.scripts.phase6_evaluate --check-only` and the data lock (pre-registration §6).
