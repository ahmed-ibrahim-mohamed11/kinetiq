from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter


# =========================================================
# 1. Paths
# =========================================================

AI_DIR = Path(__file__).resolve().parent.parent

CSV_PATH = (
    AI_DIR
    / "outputs"
    / "seated_row_keypoints.csv"
)


# =========================================================
# 2. Load CSV Data
# =========================================================

if not CSV_PATH.exists():
    raise FileNotFoundError(
        f"CSV file not found:\n{CSV_PATH}"
    )


df = pd.read_csv(CSV_PATH)

print(f"Total frames: {len(df)}")


# =========================================================
# 3. Read Important Joint Coordinates
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
# 4. Calculate Shoulder Width
# =========================================================
# We use shoulder width as a reference so the measurement
# is less affected by how far the athlete is from the camera.
# =========================================================

shoulder_width = np.linalg.norm(
    left_shoulder - right_shoulder,
    axis=1
)


# Prevent division by zero
shoulder_width = np.maximum(
    shoulder_width,
    1e-6
)


# =========================================================
# 5. Calculate Elbow Width
# =========================================================
# Horizontal distance between left and right elbow.
# During seated row, this signal changes while pulling.
# =========================================================

elbow_width = np.abs(
    left_elbow[:, 0]
    - right_elbow[:, 0]
)


# =========================================================
# 6. Normalize Movement Signal
# =========================================================

movement_signal = (
    elbow_width
    / shoulder_width
)


# =========================================================
# 7. Handle Missing / Invalid Values
# =========================================================

movement_signal = pd.Series(
    movement_signal
)

movement_signal = (
    movement_signal
    .interpolate()
    .bfill()
    .ffill()
    .to_numpy()
)


# =========================================================
# 8. Smooth Signal
# =========================================================
# Raw pose coordinates contain small frame-to-frame noise.
# Savitzky-Golay filtering makes the motion easier to detect.
# =========================================================

window_length = 31

# Window length must be odd
if window_length % 2 == 0:
    window_length += 1

# Window also cannot be longer than our data
if window_length >= len(movement_signal):

    window_length = len(movement_signal) - 1

    if window_length % 2 == 0:
        window_length -= 1


smoothed_signal = savgol_filter(
    movement_signal,
    window_length=window_length,
    polyorder=3
)


# =========================================================
# 9. Calculate Adaptive Thresholds
# =========================================================
#
# Lower part of signal = released/start position
# Higher part of signal = pulled position
#
# These are temporary offline thresholds.
# Later we will build a live version.
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
print(
    f"Low threshold : "
    f"{LOW_THRESHOLD:.4f}"
)

print(
    f"High threshold: "
    f"{HIGH_THRESHOLD:.4f}"
)

print()


# =========================================================
# 10. Rep Counter State Machine
# =========================================================
#
# States:
#
# WAITING
#     ↓
# RELEASED
#     ↓
# PULLED  -> Count Rep
#     ↓
# RELEASED
#     ↓
# Ready for next repetition
#
# Important:
# We count the repetition when the athlete reaches
# the pulled position.
# =========================================================

rep_count = 0

state = "WAITING"


for frame_number, value in enumerate(
    smoothed_signal,
    start=1
):

    # -----------------------------------------------------
    # STATE 1:
    # Waiting for athlete to reach starting position
    # -----------------------------------------------------

    if state == "WAITING":

        if value <= LOW_THRESHOLD:

            state = "RELEASED"

            print(
                f"Frame {frame_number}: "
                f"Ready / Released"
            )


    # -----------------------------------------------------
    # STATE 2:
    # Athlete was released
    # Wait until they pull
    # -----------------------------------------------------

    elif state == "RELEASED":

        if value >= HIGH_THRESHOLD:

            rep_count += 1

            state = "PULLED"

            print()
            print(
                f"REP {rep_count} DETECTED "
                f"AT FRAME {frame_number}"
            )
            print()


    # -----------------------------------------------------
    # STATE 3:
    # Athlete has pulled
    # Wait until arms return to released position
    # -----------------------------------------------------

    elif state == "PULLED":

        if value <= LOW_THRESHOLD:

            state = "RELEASED"

            print(
                f"Frame {frame_number}: "
                f"Released / Ready for next rep"
            )


# =========================================================
# 11. Final Result
# =========================================================

print()
print("============================")
print(
    f"Detected repetitions: "
    f"{rep_count}"
)
print("============================")