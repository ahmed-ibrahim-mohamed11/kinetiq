from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter


# =========================================================
# 1. Paths
# =========================================================

AI_DIR = Path(__file__).resolve().parent.parent.parent

KEYPOINTS_CSV = (
    AI_DIR
    / "outputs"
    / "seated_row_keypoints.csv"
)

OUTPUT_CSV = (
    AI_DIR
    / "outputs"
    / "seated_row_reps.csv"
)


# =========================================================
# 2. Load Keypoints Data
# =========================================================

if not KEYPOINTS_CSV.exists():
    raise FileNotFoundError(
        f"Keypoints CSV not found:\n{KEYPOINTS_CSV}"
    )

df = pd.read_csv(KEYPOINTS_CSV)

print(f"Total frames: {len(df)}")


# =========================================================
# 3. Extract Important Coordinates
# =========================================================

left_shoulder = np.column_stack([
    df["left_shoulder_x"],
    df["left_shoulder_y"]
])

right_shoulder = np.column_stack([
    df["right_shoulder_x"],
    df["right_shoulder_y"]
])

left_elbow = np.column_stack([
    df["left_elbow_x"],
    df["left_elbow_y"]
])

right_elbow = np.column_stack([
    df["right_elbow_x"],
    df["right_elbow_y"]
])


# =========================================================
# 4. Shoulder Width
# =========================================================

shoulder_width = np.linalg.norm(
    left_shoulder - right_shoulder,
    axis=1
)

shoulder_width = np.maximum(
    shoulder_width,
    1e-6
)


# =========================================================
# 5. Elbow Width
# =========================================================

elbow_width = np.abs(
    left_elbow[:, 0]
    - right_elbow[:, 0]
)


# =========================================================
# 6. Normalized Movement Signal
# =========================================================

movement_signal = (
    elbow_width
    / shoulder_width
)


# Fix missing values if any
movement_signal = (
    pd.Series(movement_signal)
    .interpolate()
    .bfill()
    .ffill()
    .to_numpy()
)


# =========================================================
# 7. Smooth Signal
# =========================================================

smoothed_signal = savgol_filter(
    movement_signal,
    window_length=31,
    polyorder=3
)


# =========================================================
# 8. Adaptive Thresholds
# =========================================================

LOW_THRESHOLD = np.percentile(
    smoothed_signal,
    30
)

HIGH_THRESHOLD = np.percentile(
    smoothed_signal,
    70
)


print()
print(f"Low threshold : {LOW_THRESHOLD:.4f}")
print(f"High threshold: {HIGH_THRESHOLD:.4f}")
print()


# =========================================================
# 9. Rep Segmentation State Machine
# =========================================================

state = "WAITING"

current_start = None
current_pull = None

rep_count = 0
repetitions = []


for frame_index, value in enumerate(
    smoothed_signal
):

    frame_number = frame_index + 1


    # -----------------------------------------------------
    # WAITING -> RELEASED
    #
    # Find the start of the first repetition
    # -----------------------------------------------------

    if state == "WAITING":

        if value <= LOW_THRESHOLD:

            current_start = frame_number

            state = "RELEASED"

            print(
                f"Start detected at frame "
                f"{frame_number}"
            )


    # -----------------------------------------------------
    # RELEASED -> PULLED
    #
    # Detect peak/pulled phase
    # -----------------------------------------------------

    elif state == "RELEASED":

        if value >= HIGH_THRESHOLD:

            current_pull = frame_number

            state = "PULLED"

            print(
                f"Pull detected at frame "
                f"{frame_number}"
            )


    # -----------------------------------------------------
    # PULLED -> RELEASED
    #
    # Complete the repetition
    # -----------------------------------------------------

    elif state == "PULLED":

        if value <= LOW_THRESHOLD:

            end_frame = frame_number

            rep_count += 1


            repetitions.append({
                "rep": rep_count,

                "start_frame":
                    current_start,

                "pull_frame":
                    current_pull,

                "end_frame":
                    end_frame,

                "pull_frames":
                    current_pull
                    - current_start,

                "return_frames":
                    end_frame
                    - current_pull,

                "total_frames":
                    end_frame
                    - current_start
            })


            print()
            print(
                f"REP {rep_count}"
            )

            print(
                f"Start: {current_start}"
            )

            print(
                f"Pull : {current_pull}"
            )

            print(
                f"End  : {end_frame}"
            )

            print()


            # Important:
            # End of previous rep becomes
            # start of next rep.
            current_start = end_frame

            current_pull = None

            state = "RELEASED"


# =========================================================
# 10. Handle Final Incomplete Rep
# =========================================================
#
# Your test video reaches the pulled position of rep 8
# very close to the end.
#
# If the video ends before the signal returns fully to the
# LOW threshold, we still store it as an incomplete rep.
#
# =========================================================

if (
    state == "PULLED"
    and current_start is not None
    and current_pull is not None
):

    rep_count += 1

    end_frame = len(df)

    repetitions.append({
        "rep": rep_count,

        "start_frame":
            current_start,

        "pull_frame":
            current_pull,

        "end_frame":
            end_frame,

        "pull_frames":
            current_pull
            - current_start,

        "return_frames":
            end_frame
            - current_pull,

        "total_frames":
            end_frame
            - current_start,

        "incomplete_return":
            True
    })

    print()
    print(
        f"REP {rep_count} "
        f"ended with video"
    )

    print(
        f"Start: {current_start}"
    )

    print(
        f"Pull : {current_pull}"
    )

    print(
        f"End  : {end_frame}"
    )


# =========================================================
# 11. Convert To DataFrame
# =========================================================

reps_df = pd.DataFrame(
    repetitions
)


# Add column if previous reps do not have it
if (
    "incomplete_return"
    not in reps_df.columns
):

    reps_df[
        "incomplete_return"
    ] = False

else:

    reps_df[
        "incomplete_return"
    ] = (
        reps_df[
            "incomplete_return"
        ]
        .fillna(False)
    )


# =========================================================
# 12. Save Result
# =========================================================

reps_df.to_csv(
    OUTPUT_CSV,
    index=False
)


# =========================================================
# 13. Final Output
# =========================================================

print()
print(
    "======================================"
)

print(
    f"Detected repetitions: "
    f"{len(reps_df)}"
)

print(
    "======================================"
)

print()
print(reps_df)

print()
print(
    f"Saved to:\n{OUTPUT_CSV}"
)