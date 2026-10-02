from pathlib import Path

import cv2
import numpy as np
import pandas as pd
from scipy.signal import savgol_filter


# =========================================================
# 1. Paths
# =========================================================

AI_DIR = Path(__file__).resolve().parent.parent

VIDEO_PATH = (
    AI_DIR
    / "data"
    / "raw"
    / "seated_row_test.mp4"
)

CSV_PATH = (
    AI_DIR
    / "outputs"
    / "seated_row_keypoints.csv"
)

OUTPUT_VIDEO = (
    AI_DIR
    / "outputs"
    / "seated_row_rep_counter.mp4"
)


# =========================================================
# 2. Check Files
# =========================================================

if not VIDEO_PATH.exists():
    raise FileNotFoundError(
        f"Video not found:\n{VIDEO_PATH}"
    )

if not CSV_PATH.exists():
    raise FileNotFoundError(
        f"CSV not found:\n{CSV_PATH}"
    )


# =========================================================
# 3. Load Keypoints
# =========================================================

df = pd.read_csv(CSV_PATH)

print(f"Total CSV frames: {len(df)}")


# =========================================================
# 4. Extract Coordinates
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
# 5. Shoulder Width
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
# 6. Elbow Width
# =========================================================

elbow_width = np.abs(
    left_elbow[:, 0]
    - right_elbow[:, 0]
)


# =========================================================
# 7. Movement Signal
# =========================================================

movement_signal = (
    elbow_width
    / shoulder_width
)


# Fix missing values
movement_signal = (
    pd.Series(movement_signal)
    .interpolate()
    .bfill()
    .ffill()
    .to_numpy()
)


# =========================================================
# 8. Smooth Signal
# =========================================================

smoothed_signal = savgol_filter(
    movement_signal,
    window_length=31,
    polyorder=3
)


# =========================================================
# 9. Thresholds
# =========================================================

LOW_THRESHOLD = np.percentile(
    smoothed_signal,
    30
)

HIGH_THRESHOLD = np.percentile(
    smoothed_signal,
    70
)


print(
    f"Low threshold : {LOW_THRESHOLD:.4f}"
)

print(
    f"High threshold: {HIGH_THRESHOLD:.4f}"
)


# =========================================================
# 10. Calculate Rep State For Every Frame
# =========================================================

rep_history = []

state_history = []

rep_count = 0

state = "WAITING"


for value in smoothed_signal:

    # -----------------------------------------
    # WAITING
    # -----------------------------------------

    if state == "WAITING":

        if value <= LOW_THRESHOLD:
            state = "RELEASED"


    # -----------------------------------------
    # RELEASED
    # -----------------------------------------

    elif state == "RELEASED":

        if value >= HIGH_THRESHOLD:

            rep_count += 1

            state = "PULLED"


    # -----------------------------------------
    # PULLED
    # -----------------------------------------

    elif state == "PULLED":

        if value <= LOW_THRESHOLD:

            state = "RELEASED"


    rep_history.append(rep_count)

    state_history.append(state)


print()
print(
    f"Detected repetitions: {rep_count}"
)


# =========================================================
# 11. Open Original Video
# =========================================================

cap = cv2.VideoCapture(
    str(VIDEO_PATH)
)

if not cap.isOpened():
    raise RuntimeError(
        "Could not open input video."
    )


fps = cap.get(
    cv2.CAP_PROP_FPS
)

width = int(
    cap.get(
        cv2.CAP_PROP_FRAME_WIDTH
    )
)

height = int(
    cap.get(
        cv2.CAP_PROP_FRAME_HEIGHT
    )
)


# =========================================================
# 12. Output Video
# =========================================================

fourcc = cv2.VideoWriter_fourcc(
    *"mp4v"
)

writer = cv2.VideoWriter(
    str(OUTPUT_VIDEO),
    fourcc,
    fps,
    (width, height)
)


# =========================================================
# 13. Window
# =========================================================

WINDOW_NAME = "GYM AI - Rep Counter"

cv2.namedWindow(
    WINDOW_NAME,
    cv2.WINDOW_NORMAL
)

cv2.resizeWindow(
    WINDOW_NAME,
    960,
    540
)


# =========================================================
# 14. Process Video
# =========================================================

frame_index = 0


while True:

    success, frame = cap.read()

    if not success:
        break


    if frame_index >= len(rep_history):
        break


    current_rep = (
        rep_history[frame_index]
    )

    current_state = (
        state_history[frame_index]
    )

    current_signal = (
        smoothed_signal[frame_index]
    )


    # =====================================================
    # Draw Dark Background Box
    # =====================================================

    cv2.rectangle(
        frame,
        (30, 30),
        (500, 220),
        (0, 0, 0),
        -1
    )


    # =====================================================
    # REPS
    # =====================================================

    cv2.putText(
        frame,
        f"REPS: {current_rep}",
        (55, 90),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.5,
        (255, 255, 255),
        3,
        cv2.LINE_AA
    )


    # =====================================================
    # STATE
    # =====================================================

    cv2.putText(
        frame,
        f"STATE: {current_state}",
        (55, 145),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.9,
        (255, 255, 255),
        2,
        cv2.LINE_AA
    )


    # =====================================================
    # Movement Signal
    # =====================================================

    cv2.putText(
        frame,
        f"SIGNAL: {current_signal:.3f}",
        (55, 190),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA
    )


    # =====================================================
    # Save
    # =====================================================

    writer.write(
        frame
    )


    # =====================================================
    # Display
    # =====================================================

    cv2.imshow(
        WINDOW_NAME,
        frame
    )


    if (
        cv2.waitKey(1)
        & 0xFF
        == ord("q")
    ):
        break


    frame_index += 1


# =========================================================
# 15. Cleanup
# =========================================================

cap.release()

writer.release()

cv2.destroyAllWindows()


print()
print("================================")
print("Finished")
print("================================")

print(
    f"Detected repetitions: "
    f"{rep_count}"
)

print()
print("Output Video:")
print(OUTPUT_VIDEO)