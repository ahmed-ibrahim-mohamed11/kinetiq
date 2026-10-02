from pathlib import Path

import cv2
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

REPS_CSV = (
    AI_DIR
    / "outputs"
    / "seated_row_reps.csv"
)

VIDEO_PATH = (
    AI_DIR
    / "data"
    / "raw"
    / "seated_row_test.mp4"
)

OUTPUT_CSV = (
    AI_DIR
    / "outputs"
    / "seated_row_form_features.csv"
)


# =========================================================
# 2. Check Files
# =========================================================

if not KEYPOINTS_CSV.exists():
    raise FileNotFoundError(
        f"Keypoints file not found:\n{KEYPOINTS_CSV}"
    )

if not REPS_CSV.exists():
    raise FileNotFoundError(
        f"Reps file not found:\n{REPS_CSV}"
    )


# =========================================================
# 3. Load Data
# =========================================================

keypoints_df = pd.read_csv(
    KEYPOINTS_CSV
)

reps_df = pd.read_csv(
    REPS_CSV
)

print(
    f"Keypoint frames: {len(keypoints_df)}"
)

print(
    f"Detected reps: {len(reps_df)}"
)


# =========================================================
# 4. Get Video FPS
# =========================================================

fps = 30.0

if VIDEO_PATH.exists():

    cap = cv2.VideoCapture(
        str(VIDEO_PATH)
    )

    detected_fps = cap.get(
        cv2.CAP_PROP_FPS
    )

    cap.release()

    if detected_fps > 0:
        fps = detected_fps


print(
    f"Video FPS: {fps:.2f}"
)


# =========================================================
# 5. Columns Used
# =========================================================

coordinate_columns = [

    "left_shoulder_x",
    "left_shoulder_y",

    "right_shoulder_x",
    "right_shoulder_y",

    "left_elbow_x",
    "left_elbow_y",

    "right_elbow_x",
    "right_elbow_y",

    "left_hip_x",
    "left_hip_y",

    "right_hip_x",
    "right_hip_y"
]


confidence_columns = [

    "left_shoulder_confidence",
    "right_shoulder_confidence",

    "left_elbow_confidence",
    "right_elbow_confidence",

    "left_hip_confidence",
    "right_hip_confidence"
]


# =========================================================
# 6. Fix Missing Values
# =========================================================

for column in (
    coordinate_columns
    + confidence_columns
):

    keypoints_df[column] = pd.to_numeric(
        keypoints_df[column],
        errors="coerce"
    )


# Interpolate coordinates only
keypoints_df[
    coordinate_columns
] = (

    keypoints_df[
        coordinate_columns
    ]

    .interpolate()

    .bfill()

    .ffill()
)


# =========================================================
# 7. Helper Function For Points
# =========================================================

def get_points(
    x_column,
    y_column
):

    return np.column_stack([

        keypoints_df[
            x_column
        ].to_numpy(dtype=float),

        keypoints_df[
            y_column
        ].to_numpy(dtype=float)
    ])


# =========================================================
# 8. Extract Body Joints
# =========================================================

left_shoulder = get_points(
    "left_shoulder_x",
    "left_shoulder_y"
)

right_shoulder = get_points(
    "right_shoulder_x",
    "right_shoulder_y"
)


left_elbow = get_points(
    "left_elbow_x",
    "left_elbow_y"
)

right_elbow = get_points(
    "right_elbow_x",
    "right_elbow_y"
)


left_hip = get_points(
    "left_hip_x",
    "left_hip_y"
)

right_hip = get_points(
    "right_hip_x",
    "right_hip_y"
)


# =========================================================
# 9. Shoulder Width
# =========================================================
#
# We use shoulder width for normalization.
#
# This makes measurements less dependent on:
#
# - camera distance
# - image resolution
# - body size
#
# =========================================================

shoulder_width = np.linalg.norm(

    left_shoulder
    -
    right_shoulder,

    axis=1
)


shoulder_width = np.maximum(
    shoulder_width,
    1e-6
)


# =========================================================
# 10. Movement Signal
# =========================================================

elbow_width = np.abs(

    left_elbow[:, 0]
    -
    right_elbow[:, 0]
)


movement_signal = (

    elbow_width
    /
    shoulder_width
)


movement_signal = (

    pd.Series(
        movement_signal
    )

    .interpolate()

    .bfill()

    .ffill()

    .to_numpy()
)


# Smooth it
smoothed_signal = savgol_filter(

    movement_signal,

    window_length=31,

    polyorder=3
)


# =========================================================
# 11. Shoulder Asymmetry Signal
# =========================================================
#
# Difference in vertical shoulder position.
#
# 0 = perfectly level
#
# Higher number = larger difference
#
# =========================================================

shoulder_asymmetry_signal = (

    np.abs(

        left_shoulder[:, 1]
        -
        right_shoulder[:, 1]
    )

    /

    shoulder_width
)


# =========================================================
# 12. Torso Lateral Shift
# =========================================================
#
# Shoulder center should stay reasonably aligned
# with hip center from this camera angle.
#
# =========================================================

shoulder_center = (

    left_shoulder
    +
    right_shoulder

) / 2


hip_center = (

    left_hip
    +
    right_hip

) / 2


torso_shift_signal = (

    np.abs(

        shoulder_center[:, 0]
        -
        hip_center[:, 0]
    )

    /

    shoulder_width
)


# =========================================================
# 13. Elbow Path Symmetry
# =========================================================
#
# Represent each elbow relative to its shoulder.
#
# Because the body is mirrored:
# the right side X direction is flipped.
#
# =========================================================

left_elbow_relative_x = (

    left_elbow[:, 0]
    -
    left_shoulder[:, 0]

) / shoulder_width


left_elbow_relative_y = (

    left_elbow[:, 1]
    -
    left_shoulder[:, 1]

) / shoulder_width


right_elbow_relative_x = (

    right_elbow[:, 0]
    -
    right_shoulder[:, 0]

) / shoulder_width


right_elbow_relative_y = (

    right_elbow[:, 1]
    -
    right_shoulder[:, 1]

) / shoulder_width


# Mirror right side
right_elbow_relative_x_mirrored = (
    -right_elbow_relative_x
)


elbow_path_asymmetry_signal = np.sqrt(

    (
        left_elbow_relative_x
        -
        right_elbow_relative_x_mirrored
    ) ** 2

    +

    (
        left_elbow_relative_y
        -
        right_elbow_relative_y
    ) ** 2
)


# =========================================================
# 14. Helper Functions
# =========================================================

def mean_value(
    signal,
    start,
    end
):

    values = signal[
        start:
        end + 1
    ]

    if len(values) == 0:
        return np.nan

    return float(
        np.nanmean(values)
    )


def max_value(
    signal,
    start,
    end
):

    values = signal[
        start:
        end + 1
    ]

    if len(values) == 0:
        return np.nan

    return float(
        np.nanmax(values)
    )


def pose_quality(
    start,
    end
):

    segment = (

        keypoints_df
        .iloc[
            start:
            end + 1
        ][
            confidence_columns
        ]
    )

    return float(

        segment
        .mean()
        .mean()
    )


def parse_bool(
    value
):

    if isinstance(
        value,
        bool
    ):
        return value

    return (
        str(value)
        .strip()
        .lower()
        in [
            "true",
            "1",
            "yes"
        ]
    )


# =========================================================
# 15. Analyze Each Rep
# =========================================================

features = []


for _, rep in reps_df.iterrows():

    rep_number = int(
        rep["rep"]
    )


    # CSV frames are 1-based.
    # Python indexes are 0-based.

    start = (
        int(
            rep["start_frame"]
        )
        - 1
    )

    pull = (
        int(
            rep["pull_frame"]
        )
        - 1
    )

    end = (
        int(
            rep["end_frame"]
        )
        - 1
    )


    incomplete_return = parse_bool(

        rep.get(
            "incomplete_return",
            False
        )
    )


    # =====================================================
    # TIMING
    # =====================================================

    pull_frames = (
        pull - start
    )

    return_frames = (
        end - pull
    )

    total_frames = (
        end - start
    )


    pull_time = (
        pull_frames
        /
        fps
    )


    if incomplete_return:

        return_time = np.nan

    else:

        return_time = (
            return_frames
            /
            fps
        )


    total_time = (
        total_frames
        /
        fps
    )


    # =====================================================
    # RANGE OF MOTION PROXY
    # =====================================================
    #
    # Use a small window around start/pull
    # instead of only one frame.
    #
    # =====================================================

    start_window_start = max(
        0,
        start - 2
    )

    start_window_end = min(
        len(smoothed_signal),
        start + 3
    )


    pull_window_start = max(
        0,
        pull - 2
    )

    pull_window_end = min(
        len(smoothed_signal),
        pull + 3
    )


    released_value = float(
        np.mean(
            smoothed_signal[
                start_window_start:
                start_window_end
            ]
        )
    )


    pulled_value = float(
        np.mean(
            smoothed_signal[
                pull_window_start:
                pull_window_end
            ]
        )
    )


    rom_proxy = (

        pulled_value
        -
        released_value
    )


    # =====================================================
    # PULL PHASE FEATURES
    # =====================================================

    pull_shoulder_asym_mean = mean_value(
        shoulder_asymmetry_signal,
        start,
        pull
    )

    pull_shoulder_asym_max = max_value(
        shoulder_asymmetry_signal,
        start,
        pull
    )


    pull_torso_shift_mean = mean_value(
        torso_shift_signal,
        start,
        pull
    )

    pull_torso_shift_max = max_value(
        torso_shift_signal,
        start,
        pull
    )


    pull_elbow_asym_mean = mean_value(
        elbow_path_asymmetry_signal,
        start,
        pull
    )

    pull_elbow_asym_max = max_value(
        elbow_path_asymmetry_signal,
        start,
        pull
    )


    pull_pose_quality = pose_quality(
        start,
        pull
    )


    # =====================================================
    # RETURN PHASE FEATURES
    # =====================================================

    if incomplete_return:

        return_shoulder_asym_mean = np.nan
        return_shoulder_asym_max = np.nan

        return_torso_shift_mean = np.nan
        return_torso_shift_max = np.nan

        return_elbow_asym_mean = np.nan
        return_elbow_asym_max = np.nan

        return_pose_quality = np.nan

    else:

        return_shoulder_asym_mean = mean_value(
            shoulder_asymmetry_signal,
            pull,
            end
        )

        return_shoulder_asym_max = max_value(
            shoulder_asymmetry_signal,
            pull,
            end
        )


        return_torso_shift_mean = mean_value(
            torso_shift_signal,
            pull,
            end
        )

        return_torso_shift_max = max_value(
            torso_shift_signal,
            pull,
            end
        )


        return_elbow_asym_mean = mean_value(
            elbow_path_asymmetry_signal,
            pull,
            end
        )

        return_elbow_asym_max = max_value(
            elbow_path_asymmetry_signal,
            pull,
            end
        )


        return_pose_quality = pose_quality(
            pull,
            end
        )


    # =====================================================
    # SAVE REP FEATURES
    # =====================================================

    features.append({

        "rep":
            rep_number,

        "start_frame":
            start + 1,

        "pull_frame":
            pull + 1,

        "end_frame":
            end + 1,

        "incomplete_return":
            incomplete_return,


        # ------------------------------
        # Timing
        # ------------------------------

        "pull_time_sec":
            pull_time,

        "return_time_sec":
            return_time,

        "total_time_sec":
            total_time,


        # ------------------------------
        # ROM
        # ------------------------------

        "rom_proxy":
            rom_proxy,


        # ------------------------------
        # Pull phase
        # ------------------------------

        "pull_shoulder_asym_mean":
            pull_shoulder_asym_mean,

        "pull_shoulder_asym_max":
            pull_shoulder_asym_max,

        "pull_torso_shift_mean":
            pull_torso_shift_mean,

        "pull_torso_shift_max":
            pull_torso_shift_max,

        "pull_elbow_asym_mean":
            pull_elbow_asym_mean,

        "pull_elbow_asym_max":
            pull_elbow_asym_max,

        "pull_pose_quality":
            pull_pose_quality,


        # ------------------------------
        # Return phase
        # ------------------------------

        "return_shoulder_asym_mean":
            return_shoulder_asym_mean,

        "return_shoulder_asym_max":
            return_shoulder_asym_max,

        "return_torso_shift_mean":
            return_torso_shift_mean,

        "return_torso_shift_max":
            return_torso_shift_max,

        "return_elbow_asym_mean":
            return_elbow_asym_mean,

        "return_elbow_asym_max":
            return_elbow_asym_max,

        "return_pose_quality":
            return_pose_quality
    })


# =========================================================
# 16. Create Feature DataFrame
# =========================================================

features_df = pd.DataFrame(
    features
)


# =========================================================
# 17. Save CSV
# =========================================================

features_df.to_csv(
    OUTPUT_CSV,
    index=False
)


# =========================================================
# 18. Print Summary
# =========================================================

print()
print(
    "================================================"
)

print(
    "       SEATED ROW FORM FEATURES"
)

print(
    "================================================"
)


for _, row in features_df.iterrows():

    print()

    print(
        f"REP {int(row['rep'])}"
    )

    print(
        f"Pull Time: "
        f"{row['pull_time_sec']:.2f} sec"
    )


    if pd.isna(
        row["return_time_sec"]
    ):

        print(
            "Return Time: INCOMPLETE"
        )

    else:

        print(
            f"Return Time: "
            f"{row['return_time_sec']:.2f} sec"
        )


    print(
        f"ROM Proxy: "
        f"{row['rom_proxy']:.3f}"
    )


    print(
        "Pull Shoulder Asymmetry: "
        f"{row['pull_shoulder_asym_mean']:.3f}"
    )


    print(
        "Pull Torso Shift: "
        f"{row['pull_torso_shift_mean']:.3f}"
    )


    print(
        "Pull Elbow Asymmetry: "
        f"{row['pull_elbow_asym_mean']:.3f}"
    )


    print(
        "Pull Pose Quality: "
        f"{row['pull_pose_quality'] * 100:.1f}%"
    )


    if row[
        "incomplete_return"
    ]:

        print(
            "Return Phase: "
            "NOT EVALUATED"
        )


print()
print(
    "================================================"
)

print(
    f"Saved to:\n{OUTPUT_CSV}"
)

print(
    "================================================"
)